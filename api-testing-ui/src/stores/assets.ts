import { defineStore } from 'pinia'

import { apiClient } from '../api/client'
import type { ApiEndpoint, LoadState } from '../api/contracts'

export const useAssetsStore = defineStore('api-assets', {
  state: () => ({ endpoints: [] as ApiEndpoint[], state: 'idle' as LoadState, error: '' }),
  actions: {
    async load(sourceRevisionId: string): Promise<void> {
      this.state = 'loading'
      this.error = ''
      try {
        const response = await apiClient.get<{ endpoints: ApiEndpoint[] }>(
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
