import { createPinia, setActivePinia } from 'pinia'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import type { ApiClient } from '../api/client'
import { useAssetsStore } from './assets'

describe('assets store endpoint details', () => {
  beforeEach(() => setActivePinia(createPinia()))

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
