import { defineStore } from 'pinia'

import { apiClient } from '../api/client'
import type { WorkspaceContext, WorkspaceResponse } from '../api/contracts'

type ContextApi = {
  get(path: string): Promise<{ data: WorkspaceResponse }>
  put(path: string, body: unknown): Promise<{ data: WorkspaceResponse }>
}

const WORKSPACE_PATH = '/api/api-testing/v1/workspace'

export const useContextStore = defineStore('context', {
  state: () => ({
    projectId: null as string | null,
    sourceRevisionId: null as string | null,
    environmentRevisionId: null as string | null,
    loading: false,
    error: '',
  }),
  actions: {
    applyWorkspace(workspace: WorkspaceContext): void {
      this.projectId = workspace.project_id
      this.sourceRevisionId = workspace.source_revision_id
      this.environmentRevisionId = workspace.environment_revision_id
    },
    async loadSavedContext(client: Pick<ContextApi, 'get'> = apiClient): Promise<void> {
      this.loading = true
      this.error = ''
      try {
        const response = await client.get(WORKSPACE_PATH)
        this.applyWorkspace(response.data.workspace)
      } catch (error) {
        this.error = error instanceof Error ? error.message : '无法恢复已保存的工作区'
      } finally {
        this.loading = false
      }
    },
    async saveContext(client: Pick<ContextApi, 'put'> = apiClient): Promise<void> {
      this.error = ''
      try {
        const response = await client.put(WORKSPACE_PATH, {
          project_id: this.projectId,
          source_revision_id: this.sourceRevisionId,
          environment_revision_id: this.environmentRevisionId,
        })
        this.applyWorkspace(response.data.workspace)
      } catch (error) {
        this.error = error instanceof Error ? error.message : '无法保存工作区'
      }
    },
  },
})
