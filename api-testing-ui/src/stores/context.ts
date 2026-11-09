import { defineStore } from 'pinia'

import { apiClient } from '../api/client'
import type {
  ContextOptionsResponse,
  EnvironmentRevisionSnapshot,
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

type EnvironmentRevisionApi = {
  get(path: string): Promise<{ data: { environment_revision: EnvironmentRevisionSnapshot } }>
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
    errorSource: '' as '' | 'workspace' | 'options' | 'save',
    readErrors: { workspace: '', options: '' },
    saveError: '',
    workspaceLoadGeneration: 0,
    optionsLoadGeneration: 0,
    saveGeneration: 0,
    selectionGeneration: 0,
    projects: [] as ProjectOption[],
    sourceRevisions: [] as SourceRevisionOption[],
    environmentRevisions: [] as EnvironmentRevisionOption[],
    environmentVariableNames: [] as string[],
    environmentVariablesLoading: false,
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
      if (contextSignature(workspace || {}) !== contextSignature(this)) this.selectionGeneration += 1
      if (!workspace) {
        this.projectId = null
        this.sourceRevisionId = null
        this.environmentRevisionId = null
        this.savedContextSignature = ''
        this.environmentVariableNames = []
        return
      }
      if (workspace.environment_revision_id !== this.environmentRevisionId) this.environmentVariableNames = []
      this.projectId = workspace.project_id
      this.sourceRevisionId = workspace.source_revision_id
      this.environmentRevisionId = workspace.environment_revision_id
      this.savedContextSignature = contextSignature(workspace)
    },
    async loadSavedContext(client: Pick<ContextApi, 'get'> = apiClient, options: { preserveSelection?: boolean } = {}): Promise<void> {
      const generation = ++this.workspaceLoadGeneration
      const selectionGeneration = this.selectionGeneration
      const saveGeneration = this.saveGeneration
      const hadSelection = Boolean(this.projectId || this.sourceRevisionId || this.environmentRevisionId)
      this.loading = true
      this.error = ''
      this.errorSource = ''
      try {
        const response = await client.get(WORKSPACE_PATH)
        const workspace = validateWorkspace(response.data.workspace, true)
        if (generation !== this.workspaceLoadGeneration) return
        // A delayed read must not replace changes made while it was in flight.
        // A manual retry can also leave an already-selected scope untouched.
        if (saveGeneration === this.saveGeneration) {
          if (selectionGeneration === this.selectionGeneration && !(options.preserveSelection && hadSelection)) this.applyWorkspace(workspace)
          else this.savedContextSignature = workspace ? contextSignature(workspace) : ''
        }
        this.readErrors.workspace = ''
      } catch (error) {
        if (generation !== this.workspaceLoadGeneration) return
        const message = error instanceof Error ? error.message : '无法恢复已保存的工作区'
        this.error = message
        this.errorSource = 'workspace'
        this.readErrors.workspace = message
      } finally {
        if (generation === this.workspaceLoadGeneration) this.loading = false
      }
    },
    async saveContext(client: Pick<ContextApi, 'put'> = apiClient): Promise<void> {
      const generation = ++this.saveGeneration
      const selectionGeneration = this.selectionGeneration
      this.error = ''
      this.errorSource = ''
      try {
        const response = await client.put(WORKSPACE_PATH, {
          project_id: this.projectId,
          source_revision_id: this.sourceRevisionId,
          environment_revision_id: this.environmentRevisionId,
        })
        const workspace = validateWorkspace(response.data.workspace, false)
        if (generation !== this.saveGeneration) return
        if (selectionGeneration === this.selectionGeneration) this.applyWorkspace(workspace)
        else this.savedContextSignature = contextSignature(workspace!)
        this.saveError = ''
      } catch (error) {
        if (generation !== this.saveGeneration) return
        const message = error instanceof Error ? error.message : '无法保存工作区'
        this.error = message
        this.errorSource = 'save'
        this.saveError = message
      }
    },
    async loadOptions(client: Pick<ContextApi, 'get'> = apiClient): Promise<void> {
      const generation = ++this.optionsLoadGeneration
      this.optionsLoading = true
      this.error = ''
      this.errorSource = ''
      try {
        const response = await client.get(CONTEXT_OPTIONS_PATH) as unknown as { data: ContextOptionsResponse }
        if (generation !== this.optionsLoadGeneration) return
        this.projects = Array.isArray(response.data.projects) ? response.data.projects : []
        this.sourceRevisions = Array.isArray(response.data.source_revisions) ? response.data.source_revisions : []
        this.environmentRevisions = Array.isArray(response.data.environment_revisions) ? response.data.environment_revisions : []
        this.readErrors.options = ''
      } catch (error) {
        if (generation !== this.optionsLoadGeneration) return
        const message = error instanceof Error ? error.message : '无法读取已保存的项目和环境'
        this.error = message
        this.errorSource = 'options'
        this.readErrors.options = message
      } finally {
        if (generation === this.optionsLoadGeneration) this.optionsLoading = false
      }
    },
    async loadEnvironmentVariableNames(revisionId: string | null, client: Pick<EnvironmentRevisionApi, 'get'> = apiClient): Promise<void> {
      this.environmentVariableNames = []
      if (!revisionId) return
      this.environmentVariablesLoading = true
      try {
        const response = await client.get(`/api/api-testing/v1/environment-revisions/${encodeURIComponent(revisionId)}`)
        const variables = response.data.environment_revision?.variables
        if (this.environmentRevisionId === revisionId && variables && typeof variables === 'object') {
          this.environmentVariableNames = Object.keys(variables).sort((a, b) => a.localeCompare(b, 'zh-CN'))
        }
      } catch {
        if (this.environmentRevisionId === revisionId) this.environmentVariableNames = []
      } finally {
        this.environmentVariablesLoading = false
      }
    },
    selectProject(projectId: string | null): void {
      if (projectId === this.projectId) return
      this.selectionGeneration += 1
      this.projectId = projectId
      this.sourceRevisionId = null
      this.environmentRevisionId = null
      this.environmentVariableNames = []
    },
    selectSourceRevision(revisionId: string | null): void {
      if (revisionId !== this.sourceRevisionId) this.selectionGeneration += 1
      this.sourceRevisionId = revisionId
    },
    selectEnvironmentRevision(revisionId: string | null): void {
      if (revisionId !== this.environmentRevisionId) this.selectionGeneration += 1
      if (revisionId !== this.environmentRevisionId) this.environmentVariableNames = []
      this.environmentRevisionId = revisionId
    },
    restoreExecutionContext(workspace: WorkspaceContext): void {
      const restored = validateWorkspace(workspace, false)
      if (contextSignature(restored!) !== contextSignature(this)) this.selectionGeneration += 1
      if (restored!.environment_revision_id !== this.environmentRevisionId) this.environmentVariableNames = []
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
