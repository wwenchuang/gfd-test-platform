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

  it('saves the Apifox token then discovers, previews and explicitly activates', async () => {
    const put = vi.spyOn(apiClient, 'put').mockResolvedValue({ data: {
      credential: { provider: 'apifox', configured: true, fingerprint: 'a1b2c3d4e5f6', updated_at: '2026-08-10T10:00:00Z' },
    } })
    const post = vi.spyOn(apiClient, 'post')
      .mockResolvedValueOnce({ data: { projects: [{ id: '5904970', name: '3D', description: '', team_name: '研发' }] } })
      .mockResolvedValueOnce({ data: { context: {
        project: { id: '5904970', name: '3D', description: '', team_name: '研发' },
        branches: [{ id: '', name: '主分支（默认）', is_default: true }],
        environments: [{ id: '33831678', name: '生产环境（新）-腾讯云', services: [], variables: [] }],
        cli_version: '2.2.8',
      } } })
      .mockResolvedValueOnce({ data: { preview: {
        source_preview: { id: 'preview-1', source_id: 'source-1', added_count: 3, changed_count: 1, removed_count: 0 },
        environment_candidate: { name: '生产环境（新）-腾讯云', secret_placeholders: ['ZXBToken'] },
      } } })
      .mockResolvedValueOnce({ data: {
        source_revision: { id: 'revision-1', source_id: 'source-1', revision_number: 1, endpoints: [] },
        environment: { id: 'environment-1', revision_id: 'environment-revision-1', revision: 1, name: '生产环境（新）-腾讯云' },
        workspace: { project_id: 'project-1', source_revision_id: 'revision-1', environment_revision_id: 'environment-revision-1' },
        secret_placeholders: ['ZXBToken'],
      } })
    const store = useSetupStore()

    await store.saveApifoxToken('afxp_secret')
    await store.discoverApifoxProjects()
    await store.discoverApifoxContext('5904970', '33831678')
    await store.previewApifox({
      project_id: 'project-1', source_id: null, apifox_project_id: '5904970',
      branch_id: '', environment_id: '33831678',
    })

    expect(store.activeRevision).toBeNull()
    await store.activateApifoxPreview()

    expect(put).toHaveBeenCalledWith('/api/api-testing/v1/providers/apifox/credential', { token: 'afxp_secret' })
    expect(post.mock.calls.map(call => call[0])).toEqual([
      '/api/api-testing/v1/providers/apifox/projects',
      '/api/api-testing/v1/providers/apifox/context',
      '/api/api-testing/v1/sources/apifox/preview',
      '/api/api-testing/v1/sources/apifox/preview-1/activate',
    ])
    expect(store.activeRevision?.id).toBe('revision-1')
    expect(store.environment?.revision_id).toBe('environment-revision-1')
    expect(store.message).toContain('已保存')
    expect(JSON.stringify(store.$state)).not.toContain('afxp_secret')
  })

  it('tracks the active Apifox read operation without hiding the explicit save step', async () => {
    const projects = deferred<{ data: { projects: Array<{ id: string; name: string; description: string; team_name: string }> } }>()
    const context = deferred<{ data: { context: {
      project: { id: string; name: string; description: string; team_name: string }
      branches: Array<{ id: string; name: string; is_default: boolean }>
      environments: Array<{ id: string; name: string; services: never[]; variables: never[] }>
      cli_version: string
    } } }>()
    const preview = deferred<{ data: { preview: {
      source_preview: { id: string; project_id: string; source_id: string; previous_revision_id: null; candidate_revision_id: string; added_count: number; changed_count: number; removed_count: number; changes: never[] }
      environment_candidate: { name: string; secret_placeholders: string[] }
    } } }>()
    vi.spyOn(apiClient, 'post')
      .mockReturnValueOnce(projects.promise)
      .mockReturnValueOnce(context.promise)
      .mockReturnValueOnce(preview.promise)
    const store = useSetupStore()

    const projectsRequest = store.discoverApifoxProjects()
    expect(store.apifoxOperation).toBe('loading_projects')
    projects.resolve({ data: { projects: [] } })
    await projectsRequest
    expect(store.apifoxOperation).toBeNull()

    const contextRequest = store.discoverApifoxContext('5904970')
    expect(store.apifoxOperation).toBe('loading_context')
    context.resolve({ data: { context: {
      project: { id: '5904970', name: '3D', description: '', team_name: '' },
      branches: [{ id: '', name: '主分支（默认）', is_default: true }],
      environments: [{ id: '33831678', name: '生产环境', services: [], variables: [] }],
      cli_version: '2.2.8',
    } } })
    await contextRequest
    expect(store.apifoxOperation).toBeNull()

    const previewRequest = store.previewApifox({
      project_id: 'project-1', source_id: null, apifox_project_id: '5904970',
      branch_id: '', environment_id: '33831678',
    })
    expect(store.apifoxOperation).toBe('checking_update')
    preview.resolve({ data: { preview: {
      source_preview: { id: 'preview-1', project_id: 'project-1', source_id: 'source-1', previous_revision_id: null, candidate_revision_id: 'candidate-1', added_count: 3, changed_count: 0, removed_count: 0, changes: [] },
      environment_candidate: { name: '生产环境', secret_placeholders: [] },
    } } })
    await previewRequest

    expect(store.apifoxOperation).toBeNull()
    expect(store.apifoxPreview?.source_preview.id).toBe('preview-1')
    expect(store.activeRevision).toBeNull()
  })

  it('loads project environment assets and their revision history', async () => {
    const get = vi.spyOn(apiClient, 'get')
      .mockResolvedValueOnce({ data: { environments: [{
        id: 'environment-1', project_id: 'project-1', source_id: 'source-1',
        active_revision_id: 'environment-revision-2', source_revision_id: 'source-revision-2',
        revision: 2, name: '生产环境', description: '腾讯云', status: 'active',
        service_count: 2, public_variable_count: 3, secret_count: 1,
        created_at: '2026-08-10T10:00:00Z', updated_at: '2026-08-13T10:00:00Z',
      }] } })
      .mockResolvedValueOnce({ data: { revisions: [{
        id: 'environment-revision-2', environment_id: 'environment-1',
        source_revision_id: 'source-revision-2', revision: 2, name: '生产环境',
        description: '腾讯云', status: 'active', created_at: '2026-08-13T10:00:00Z',
        updated_at: '2026-08-13T10:00:00Z',
      }] } })
    const store = useSetupStore()

    await store.loadEnvironmentAssets('project-1')
    await store.loadEnvironmentHistory('environment-1')

    expect(get.mock.calls).toEqual([
      ['/api/api-testing/v1/environments?project_id=project-1&status=active'],
      ['/api/api-testing/v1/environments/environment-1/revisions'],
    ])
    expect(store.environmentAssets[0].name).toBe('生产环境')
    expect(store.environmentHistory[0].revision).toBe(2)
  })

  it('archives and restores stable environment assets without deleting revisions', async () => {
    const archived = {
      id: 'environment-1', project_id: 'project-1', source_id: null,
      active_revision_id: 'environment-revision-1', source_revision_id: null,
      revision: 1, name: '测试环境', description: '', status: 'archived' as const,
      service_count: 1, public_variable_count: 0, secret_count: 0,
      created_at: '2026-08-10T10:00:00Z', updated_at: '2026-08-13T10:00:00Z',
    }
    const restored = { ...archived, status: 'active' as const }
    const remove = vi.spyOn(apiClient, 'delete').mockResolvedValue({ data: { environment: archived } })
    const post = vi.spyOn(apiClient, 'post').mockResolvedValue({ data: { environment: restored } })
    const store = useSetupStore()
    store.environmentAssets = [{ ...restored }]

    await store.archiveEnvironment('environment-1')
    expect(store.environmentAssets[0].status).toBe('archived')
    await store.restoreEnvironment('environment-1')

    expect(remove).toHaveBeenCalledWith('/api/api-testing/v1/environments/environment-1')
    expect(post).toHaveBeenCalledWith('/api/api-testing/v1/environments/environment-1/restore', {})
    expect(store.environmentAssets[0].status).toBe('active')
  })
})

function deferred<T>() {
  let resolve!: (value: T) => void
  const promise = new Promise<T>((done) => { resolve = done })
  return { promise, resolve }
}
