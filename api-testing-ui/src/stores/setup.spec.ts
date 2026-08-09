import { beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'

import { apiClient } from '../api/client'
import { useSetupStore } from './setup'

describe('setup store', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    vi.restoreAllMocks()
  })

  it('previews then explicitly activates an imported OpenAPI revision', async () => {
    const post = vi.spyOn(apiClient, 'post')
      .mockResolvedValueOnce({ data: { preview: { id: 'preview-1', source_id: 'source-1', added_count: 3, changed_count: 0, removed_count: 0 } } })
      .mockResolvedValueOnce({ data: { source_revision: { id: 'revision-1', source_id: 'source-1', revision_number: 1, endpoints: [] } } })
    const store = useSetupStore()

    await store.previewSource('project-1', null, { openapi: '3.0.3', info: { title: '3D' }, paths: {} })
    await store.activatePreview()

    expect(post.mock.calls).toEqual([
      ['/api/api-testing/v1/sources/preview', { project_id: 'project-1', source_id: null, document: { openapi: '3.0.3', info: { title: '3D' }, paths: {} } }],
      ['/api/api-testing/v1/sources/preview-1/activate', {}],
    ])
    expect(store.activeRevision?.id).toBe('revision-1')
  })

  it('clears plaintext secrets from memory after an environment revision is saved', async () => {
    const post = vi.spyOn(apiClient, 'post').mockResolvedValue({ data: { environment: {
      id: 'environment-1', project_id: 'project-1', revision_id: 'environment-revision-1', revision: 1,
      source_id: null, source_revision_id: null, name: '生产环境', description: '', status: 'active',
      services: {}, variables: {}, default_headers: {},
    } } })
    const store = useSetupStore()
    store.secretUpdates = { ZXBToken: 'business-secret-token' }

    await store.saveEnvironment(null, {
      project_id: 'project-1', name: '生产环境', services: [{ name: 'default', base_url: 'https://example.test' }],
      variables: { Biz: 'ZXB' }, default_headers: { Authorization: 'Bearer {{ZXBToken}}' },
    })

    expect(post.mock.calls[1][1]).toMatchObject({ secret_updates: { ZXBToken: 'business-secret-token' } })
    expect(JSON.stringify(store.$state)).not.toContain('business-secret-token')
  })
})
