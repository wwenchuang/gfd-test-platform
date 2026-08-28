import { defineStore } from 'pinia'

import { apiClient, type ApiClient } from '../api/client'
import type { ApiEndpoint, LoadState } from '../api/contracts'

export const useAssetsStore = defineStore('api-assets', {
  state: () => ({ endpoints: [] as ApiEndpoint[], state: 'idle' as LoadState, error: '' }),
  actions: {
    async load(sourceRevisionId: string, client: Pick<ApiClient, 'get'> = apiClient): Promise<void> {
      this.state = 'loading'
      this.error = ''
      try {
        const response = await client.get<{ endpoints: ApiEndpoint[] }>(
          `/api/api-testing/v1/endpoints?source_revision_id=${encodeURIComponent(sourceRevisionId)}`,
        )
        this.endpoints = response.data.endpoints
        this.state = this.endpoints.length ? 'ready' : 'empty'
      } catch (error) {
        this.endpoints = []
        this.state = 'failed'
        this.error = error instanceof Error ? error.message : '接口读取失败'
      }
    },
  },
})
