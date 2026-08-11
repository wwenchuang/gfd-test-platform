import { defineStore } from 'pinia'

import { apiClient } from '../api/client'
import type {
  ContextOptionsResponse,
  EnvironmentRevisionOption,
  ProjectOption,
  SourceRevisionOption,
  WorkspaceContext,
  WorkspaceResponse,
} from '../api/contracts'

type ContextApi = {
  get(path: string): Promise<{ data: WorkspaceResponse }>
  put(path: string, body: unknown): Promise<{ data: WorkspaceResponse }>
}

const WORKSPACE_PATH = '/api/api-testing/v1/workspace'
const CONTEXT_OPTIONS_PATH = '/api/api-testing/v1/context-options'

export const useContextStore = defineStore('context', {
  state: () => ({
    projectId: null as string | null,
    sourceRevisionId: null as string | null,
    environmentRevisionId: null as string | null,
    loading: false,
    optionsLoading: false,
    error: '',
    projects: [] as ProjectOption[],
    sourceRevisions: [] as SourceRevisionOption[],
    environmentRevisions: [] as EnvironmentRevisionOption[],
    savedContextSignature: '',
  }),
  getters: {
    isSaved: state => Boolean(
      state.projectId && state.sourceRevisionId && state.environmentRevisionId
      && state.savedContextSignature === contextSignature(state),
    ),
  },
  actions: {
    applyWorkspace(workspace: WorkspaceContext | null): void {
      if (!workspace) {
        this.projectId = null
        this.sourceRevisionId = null
        this.environmentRevisionId = null
        this.savedContextSignature = ''
        return
      }
      this.projectId = workspace.project_id
      this.sourceRevisionId = workspace.source_revision_id
      this.environmentRevisionId = workspace.environment_revision_id
      this.savedContextSignature = contextSignature(workspace)
    },
    async loadSavedContext(client: Pick<ContextApi, 'get'> = apiClient): Promise<void> {
      this.loading = true
      this.error = ''
      try {
        const response = await client.get(WORKSPACE_PATH)
        this.applyWorkspace(validateWorkspace(response.data.workspace, true))
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
        this.applyWorkspace(validateWorkspace(response.data.workspace, false))
      } catch (error) {
        this.error = error instanceof Error ? error.message : '无法保存工作区'
      }
    },
    async loadOptions(client: Pick<ContextApi, 'get'> = apiClient): Promise<void> {
      this.optionsLoading = true
      this.error = ''
      try {
        const response = await client.get(CONTEXT_OPTIONS_PATH) as unknown as { data: ContextOptionsResponse }
        this.projects = Array.isArray(response.data.projects) ? response.data.projects : []
        this.sourceRevisions = Array.isArray(response.data.source_revisions) ? response.data.source_revisions : []
        this.environmentRevisions = Array.isArray(response.data.environment_revisions) ? response.data.environment_revisions : []
      } catch (error) {
        this.error = error instanceof Error ? error.message : '无法读取已保存的项目和环境'
      } finally {
        this.optionsLoading = false
      }
    },
    selectProject(projectId: string | null): void {
      if (projectId === this.projectId) return
      this.projectId = projectId
      this.sourceRevisionId = null
      this.environmentRevisionId = null
    },
    selectSourceRevision(revisionId: string | null): void {
      this.sourceRevisionId = revisionId
    },
    selectEnvironmentRevision(revisionId: string | null): void {
      this.environmentRevisionId = revisionId
    },
    restoreExecutionContext(workspace: WorkspaceContext): void {
      const restored = validateWorkspace(workspace, false)
      this.projectId = restored!.project_id
      this.sourceRevisionId = restored!.source_revision_id
      this.environmentRevisionId = restored!.environment_revision_id
    },
  },
})

function contextSignature(value: { projectId?: string | null; sourceRevisionId?: string | null; environmentRevisionId?: string | null; project_id?: string | null; source_revision_id?: string | null; environment_revision_id?: string | null }): string {
  return JSON.stringify([
    value.projectId ?? value.project_id ?? null,
    value.sourceRevisionId ?? value.source_revision_id ?? null,
    value.environmentRevisionId ?? value.environment_revision_id ?? null,
  ])
}

function validateWorkspace(value: unknown, allowEmpty: boolean): WorkspaceContext | null {
  if (value === null && allowEmpty) return null
  if (!value || typeof value !== 'object') throw new Error(allowEmpty ? '工作区响应无效' : '工作区保存响应无效')
  const workspace = value as Partial<WorkspaceContext>
  const fields = [workspace.project_id, workspace.source_revision_id, workspace.environment_revision_id]
  if (fields.some(field => field !== null && typeof field !== 'string')) {
    throw new Error(allowEmpty ? '工作区响应无效' : '工作区保存响应无效')
  }
  return workspace as WorkspaceContext
}
