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

  it('discards late endpoint details after the source changes', async () => {
    const store = useAssetsStore()
    store.endpoints = [{ id: 'old', method: 'GET', path: '/old', summary: 'old', tags: [], operation: {} }]
    let resolveOld!: (value: unknown) => void
    const detail = store.ensureEndpointDetail('old', { get: vi.fn(() => new Promise(resolve => { resolveOld = resolve })) } as unknown as Pick<ApiClient, 'get'>)
    store.invalidateLoad()
    store.endpoints = [{ id: 'new', method: 'GET', path: '/new', summary: 'new', tags: [], operation: {} }]
    resolveOld({ data: { endpoint: { id: 'old', operation: { description: 'stale' } } } })
    expect(await detail).toBeNull()
    expect(store.endpoints.map(item => item.id)).toEqual(['new'])
    expect(store.detailedEndpointIds).toEqual({})
  })

  it('discards late detail errors after the source changes', async () => {
    const store = useAssetsStore()
    store.endpoints = [{ id: 'old', method: 'GET', path: '/old', summary: 'old', tags: [], operation: {} }]
    let failOld!: (error: Error) => void
    const detail = store.ensureEndpointDetail('old', { get: vi.fn(() => new Promise((_, reject) => { failOld = reject })) } as unknown as Pick<ApiClient, 'get'>)
    store.invalidateLoad()
    store.endpoints = []
    failOld(new Error('旧详情失败'))
    expect(await detail).toBeNull()
    expect(store.error).toBe('')
    expect(store.detailError).toBe('')
  })

  it('does not expose an older detail failure after a newer detail succeeds', async () => {
    const store = useAssetsStore()
    store.endpoints = [
      { id: 'a', method: 'GET', path: '/a', summary: 'a', tags: [], operation: {} },
      { id: 'b', method: 'GET', path: '/b', summary: 'b', tags: [], operation: {} },
    ]
    let failOld!: (error: Error) => void
    const get = vi.fn().mockImplementationOnce(() => new Promise((_, reject) => { failOld = reject }))
      .mockResolvedValueOnce({ data: { endpoint: store.endpoints[1] } })
    const client = { get } as unknown as Pick<ApiClient, 'get'>
    const old = store.ensureEndpointDetail('a', client)
    await store.ensureEndpointDetail('b', client)
    failOld(new Error('旧详情失败')); await old
    expect(store.error).toBe('')
    expect(store.detailError).toBe('')
  })

  it('retains the newest detail when the same endpoint is fetched twice', async () => {
    const store = useAssetsStore()
    store.endpoints = [{ id: 'a', method: 'GET', path: '/a', summary: 'a', tags: [], operation: {} }]
    let finishOld!: (result: unknown) => void
    const get = vi.fn().mockImplementationOnce(() => new Promise(resolve => { finishOld = resolve }))
      .mockResolvedValueOnce({ data: { endpoint: { ...store.endpoints[0], summary: 'new' } } })
    const client = { get } as unknown as Pick<ApiClient, 'get'>
    const old = store.ensureEndpointDetail('a', client)
    await store.ensureEndpointDetail('a', client)
    finishOld({ data: { endpoint: { ...store.endpoints[0], summary: 'old' } } })
    await old
    expect(store.endpoints[0].summary).toBe('new')
    expect((await store.ensureEndpointDetail('a', client))?.summary).toBe('new')
  })

  it('keeps list failures separate from the latest detail error', async () => {
    const store = useAssetsStore()
    store.error = '列表服务失败'
    store.endpoints = [{ id: 'a', method: 'GET', path: '/a', summary: 'a', tags: [], operation: {} }]
    const get = vi.fn().mockRejectedValue(new Error('详情服务失败'))
    const client = { get } as unknown as Pick<ApiClient, 'get'>
    expect(await store.ensureEndpointDetail('a', client)).toBeNull()
    expect(store.error).toBe('列表服务失败')
    expect(store.detailError).toBe('详情服务失败')
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

  it('rejects cached detail after a same-source account change until the source is reloaded', async () => {
    let token = 'account-a'
    vi.stubGlobal('sessionStorage', { getItem: () => token })
    try {
      const endpoint = { id: 'a', method: 'GET', path: '/a', summary: 'a', tags: [], operation: {} }
      const get = vi.fn().mockResolvedValueOnce({ data: { endpoints: [endpoint] } })
        .mockResolvedValueOnce({ data: { endpoint: { ...endpoint, operation: { description: 'old account' } } } })
      const client = { get } as unknown as Pick<ApiClient, 'get'>
      const store = useAssetsStore()
      await store.load('source-1', client)
      expect((await store.ensureEndpointDetail('a', client))?.operation).toEqual({ description: 'old account' })
      token = 'account-b'
      expect(await store.ensureEndpointDetail('a', client)).toBeNull()
      expect(get).toHaveBeenCalledTimes(2)
    } finally { vi.unstubAllGlobals() }
  })

  it('does not cache detail that finishes after account change', async () => {
    let token = 'account-a'
    vi.stubGlobal('sessionStorage', { getItem: () => token })
    try {
      const endpoint = { id: 'a', method: 'GET', path: '/a', summary: 'a', tags: [], operation: {} }
      let finish!: (value: unknown) => void
      const get = vi.fn().mockResolvedValueOnce({ data: { endpoints: [endpoint] } })
        .mockImplementationOnce(() => new Promise(resolve => { finish = resolve }))
      const client = { get } as unknown as Pick<ApiClient, 'get'>
      const store = useAssetsStore()
      await store.load('source-1', client)
      const pending = store.ensureEndpointDetail('a', client)
      token = 'account-b'
      finish({ data: { endpoint: { ...endpoint, operation: { description: 'old account' } } } })
      expect(await pending).toBeNull()
      expect(store.endpoints[0].operation).toEqual({})
      expect(store.detailedEndpointIds).toEqual({})
    } finally { vi.unstubAllGlobals() }
  })

  it('does not publish a source list that finishes after account change', async () => {
    let token = 'account-a'
    vi.stubGlobal('sessionStorage', { getItem: () => token })
    try {
      let finish!: (value: unknown) => void
      const client = { get: vi.fn(() => new Promise(resolve => { finish = resolve })) } as unknown as Pick<ApiClient, 'get'>
      const store = useAssetsStore()
      const pending = store.load('source-1', client)
      token = 'account-b'
      finish({ data: { endpoints: [{ id: 'old', method: 'GET', path: '/old', tags: [], operation: {} }] } })
      expect(await pending).toBe(false)
      expect(store.endpoints).toEqual([])
    } finally { vi.unstubAllGlobals() }
  })
})
