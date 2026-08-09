import { beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'

import { apiClient } from '../api/client'
import type { CaseVersion } from '../api/contracts'
import { useCasesStore } from './cases'

const VERSION = {
  id: 'version-1', case_id: 'case-1', project_id: 'project-1', endpoint_id: 'endpoint-1',
  status: 'draft', origin: 'ai', version: 1, validation_summary: {},
  created_at: '2026-08-09T00:00:00Z', updated_at: '2026-08-09T00:00:00Z',
  name: '收藏列表', purpose: '验证收藏列表', priority: 'P1',
  request: { method: 'GET', path: '/favorite/list', service: 'default', path_params: {}, query: {}, headers: {}, cookies: {}, body: null },
  data_rows: [], assertions: [], extractions: [], dependencies: [], processing: { pre: [], post: [] },
} as unknown as CaseVersion

describe('cases store', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    vi.restoreAllMocks()
  })

  it('sends only the public CaseDraft fields when saving a loaded AI version', async () => {
    const post = vi.spyOn(apiClient, 'post').mockResolvedValue({ data: { case_version: { ...VERSION, version: 2 } } })
    const get = vi.spyOn(apiClient, 'get').mockResolvedValue({ data: { case_version: VERSION } })
    const store = useCasesStore()

    await store.loadVersion(VERSION.id)
    await store.save(VERSION.endpoint_id)

    expect(get).toHaveBeenCalledOnce()
    expect(post.mock.calls[0][1]).toEqual({
      case: expect.objectContaining({ name: '收藏列表', request: expect.any(Object) }),
    })
    expect(Object.keys((post.mock.calls[0][1] as { case: object }).case).sort()).toEqual([
      'assertions', 'data_rows', 'dependencies', 'extractions', 'name', 'priority', 'processing', 'purpose', 'request',
    ])
  })
})
