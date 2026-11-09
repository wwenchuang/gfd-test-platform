import { defineStore } from 'pinia'

import { apiClient, type ApiClient } from '../api/client'
import type { ApiEndpoint, LoadState } from '../api/contracts'

// Associate cached source data with its login without storing credentials in Pinia.
const storeSessions = new WeakMap<object, string | null>()
function sessionIdentity(): string | null {
  try { return globalThis.sessionStorage?.getItem('sessionToken') || null } catch { return null }
}

export const useAssetsStore = defineStore('api-assets', {
  state: () => ({
    endpoints: [] as ApiEndpoint[],
    detailedEndpointIds: {} as Record<string, true>,
    state: 'idle' as LoadState,
    error: '',
    detailError: '',
    loadGeneration: 0,
    detailGeneration: 0,
  }),
  actions: {
    invalidateLoad(): void {
      this.loadGeneration += 1
      if (this.state === 'loading') this.state = 'idle'
    },
    async load(sourceRevisionId: string, client: Pick<ApiClient, 'get'> = apiClient): Promise<void | false> {
      const generation = ++this.loadGeneration
      const session = sessionIdentity()
      this.state = 'loading'
      this.error = ''
      this.detailedEndpointIds = {}
      try {
        const response = await client.get<{ endpoints: ApiEndpoint[] }>(
          `/api/api-testing/v1/endpoints?source_revision_id=${encodeURIComponent(sourceRevisionId)}`,
        )
        if (generation !== this.loadGeneration || session !== sessionIdentity()) return false
        storeSessions.set(this, session)
        this.endpoints = response.data.endpoints
        this.state = this.endpoints.length ? 'ready' : 'empty'
      } catch (error) {
        if (generation !== this.loadGeneration || session !== sessionIdentity()) return false
        this.endpoints = []
        this.state = 'failed'
        this.error = error instanceof Error ? error.message : '接口读取失败'
      }
    },
    async ensureEndpointDetail(
      endpointId: string,
      client: Pick<ApiClient, 'get'> = apiClient,
    ): Promise<ApiEndpoint | null> {
      const generation = this.loadGeneration
      const detailGeneration = ++this.detailGeneration
      const session = sessionIdentity()
      this.detailError = ''
      if (storeSessions.has(this) && storeSessions.get(this) !== session) {
        this.detailError = '登录状态已变化，请重新读取接口'
        return null
      }
      const current = this.endpoints.find(item => item.id === endpointId)
      if (!current) return null
      if (this.detailedEndpointIds[endpointId]) return current
      // Programmatically supplied endpoints (tests or deep-link restoration) may
      // predate the lightweight-list contract and omit the marker field entirely.
      if (current.operation === undefined) return current
      try {
        const response = await client.get<{ endpoint: ApiEndpoint }>(
          `/api/api-testing/v1/endpoints/${encodeURIComponent(endpointId)}`,
        )
        if (generation !== this.loadGeneration || detailGeneration !== this.detailGeneration || session !== sessionIdentity()) return null
        const detailed = response.data.endpoint
        const index = this.endpoints.findIndex(item => item.id === endpointId)
        if (index < 0) return null
        this.endpoints[index] = detailed
        this.detailedEndpointIds[endpointId] = true
        return detailed
      } catch (error) {
        if (generation !== this.loadGeneration || detailGeneration !== this.detailGeneration || session !== sessionIdentity()) return null
        this.detailError = error instanceof Error ? error.message : '接口详情读取失败'
        return null
      }
    },
  },
})
