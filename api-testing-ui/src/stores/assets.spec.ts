import { createPinia, setActivePinia } from 'pinia'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import type { ApiClient } from '../api/client'
import { useAssetsStore } from './assets'

describe('assets store endpoint details', () => {
  beforeEach(() => setActivePinia(createPinia()))

  it('does not replace current source data or error with a late older response', async () => {
    let resolveOld!: (value: unknown) => void
    const get = vi.fn().mockImplementationOnce(() => new Promise(resolve => { resolveOld = resolve }))
      .mockResolvedValueOnce({ data: { endpoints: [{ id: 'new', method: 'GET', path: '/new', summary: '', tags: [] }] } })
    const store = useAssetsStore()
    const old = store.load('old', { get } as unknown as Pick<ApiClient, 'get'>)
    await store.load('new', { get } as unknown as Pick<ApiClient, 'get'>)
    resolveOld({ data: { endpoints: [{ id: 'old', method: 'GET', path: '/old', summary: '', tags: [] }] } })
    expect(await old).toBe(false)
    expect(store.endpoints.map(item => item.id)).toEqual(['new'])
  })

  it('ignores an older failed request after the current source loads', async () => {
    let failOld!: (error: Error) => void
    const get = vi.fn().mockImplementationOnce(() => new Promise((_, reject) => { failOld = reject }))
      .mockResolvedValueOnce({ data: { endpoints: [{ id: 'new', method: 'GET', path: '/new', summary: '', tags: [] }] } })
    const store = useAssetsStore()
    const old = store.load('old', { get } as unknown as Pick<ApiClient, 'get'>)
    await store.load('new', { get } as unknown as Pick<ApiClient, 'get'>)
    failOld(new Error('旧请求 502')); await old
    expect(store.state).toBe('ready')
    expect(store.error).toBe('')
    expect(store.endpoints.map(item => item.id)).toEqual(['new'])
  })

  it('loads lightweight rows first and fetches one operation on demand', async () => {
    const get = vi.fn(async (path: string) => {
      if (path.includes('?source_revision_id=')) {
        return { data: { endpoints: [{
          id: 'endpoint-1', method: 'POST', path: '/devices', summary: '创建设备',
          tags: [], operation: {},
        }] } }
      }
      return { data: { endpoint: {
        id: 'endpoint-1', method: 'POST', path: '/devices', summary: '创建设备',
        tags: [], operation: { requestBody: { required: true } },
      } } }
    })
    const client = { get } as unknown as Pick<ApiClient, 'get'>
    const store = useAssetsStore()

    await store.load('revision-1', client)
    const detailed = await store.ensureEndpointDetail('endpoint-1', client)
    await store.ensureEndpointDetail('endpoint-1', client)

    expect(store.endpoints[0].operation).toEqual({ requestBody: { required: true } })
    expect(detailed?.operation).toEqual({ requestBody: { required: true } })
    expect(get).toHaveBeenCalledTimes(2)
  })
})
