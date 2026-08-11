import { defineStore } from 'pinia'

import { apiClient } from '../api/client'
import type {
  ApifoxActivation,
  ApifoxProject,
  ApifoxProjectContext,
  ApifoxRefreshPreview,
  EnvironmentView,
  ProviderCredential,
  SourcePreview,
  SourceRevision,
} from '../api/contracts'

export interface EnvironmentPayload {
  project_id: string
  source_id?: string | null
  source_revision_id?: string | null
  name: string
  description?: string
  services: Array<Record<string, unknown>> | Record<string, unknown>
  variables: Record<string, unknown>
  default_headers: Record<string, string>
}

export interface ApifoxPreviewPayload {
  project_id: string
  source_id: string | null
  apifox_project_id: string
  branch_id: string
  environment_id: string
}

export const useSetupStore = defineStore('api-setup', {
  state: () => ({
    preview: null as SourcePreview | null,
    activeRevision: null as SourceRevision | null,
    environment: null as EnvironmentView | null,
    credential: null as ProviderCredential | null,
    apifoxProjects: [] as ApifoxProject[],
    apifoxContext: null as ApifoxProjectContext | null,
    apifoxPreview: null as ApifoxRefreshPreview | null,
    secretPlaceholders: [] as string[],
    secretUpdates: {} as Record<string, string>,
    busy: false,
    error: '',
    message: '',
  }),
  actions: {
    async loadApifoxCredential(): Promise<ProviderCredential> {
      const response = await apiClient.get<{ credential: ProviderCredential }>(
        '/api/api-testing/v1/providers/apifox/credential',
      )
      this.credential = response.data.credential
      return this.credential
    },
    async saveApifoxToken(token: string): Promise<ProviderCredential> {
      this.busy = true
      this.error = ''
      this.message = ''
      try {
        const response = await apiClient.put<{ credential: ProviderCredential }>(
          '/api/api-testing/v1/providers/apifox/credential', { token: token.trim() },
        )
        this.credential = response.data.credential
        this.message = 'Apifox 访问令牌已安全保存'
        return this.credential
      } catch (error) {
        this.error = error instanceof Error ? error.message : 'Apifox 访问令牌保存失败'
        throw error
      } finally {
        this.busy = false
      }
    },
    async discoverApifoxProjects(): Promise<ApifoxProject[]> {
      this.busy = true
      this.error = ''
      this.message = ''
      try {
        const response = await apiClient.post<{ projects: ApifoxProject[] }>(
          '/api/api-testing/v1/providers/apifox/projects', {},
        )
        this.apifoxProjects = response.data.projects
        this.message = this.apifoxProjects.length ? `已读取 ${this.apifoxProjects.length} 个 Apifox 项目` : '当前令牌下没有可用项目'
        return this.apifoxProjects
      } catch (error) {
        this.error = error instanceof Error ? error.message : 'Apifox 项目读取失败'
        throw error
      } finally {
        this.busy = false
      }
    },
    async discoverApifoxContext(projectId: string, environmentId = ''): Promise<ApifoxProjectContext> {
      this.busy = true
      this.error = ''
      this.message = ''
      try {
        const response = await apiClient.post<{ context: ApifoxProjectContext }>(
          '/api/api-testing/v1/providers/apifox/context', {
            project_id: projectId, environment_id: environmentId,
          },
        )
        this.apifoxContext = response.data.context
        this.message = `已读取 ${this.apifoxContext.environments.length} 个环境`
        return this.apifoxContext
      } catch (error) {
        this.error = error instanceof Error ? error.message : 'Apifox 环境读取失败'
        throw error
      } finally {
        this.busy = false
      }
    },
    async previewApifox(payload: ApifoxPreviewPayload): Promise<ApifoxRefreshPreview> {
      this.busy = true
      this.error = ''
      this.message = ''
      try {
        const response = await apiClient.post<{ preview: ApifoxRefreshPreview }>(
          '/api/api-testing/v1/sources/apifox/preview', payload,
        )
        this.apifoxPreview = response.data.preview
        this.preview = this.apifoxPreview.source_preview
        this.secretPlaceholders = [...this.apifoxPreview.environment_candidate.secret_placeholders]
        this.message = '已读取接口和环境变化，确认后才会保存'
        return this.apifoxPreview
      } catch (error) {
        this.error = error instanceof Error ? error.message : 'Apifox 更新检查失败'
        throw error
      } finally {
        this.busy = false
      }
    },
    async activateApifoxPreview(): Promise<ApifoxActivation> {
      if (!this.apifoxPreview) throw new Error('请先检查 Apifox 更新')
      this.busy = true
      this.error = ''
      try {
        const response = await apiClient.post<ApifoxActivation>(
          `/api/api-testing/v1/sources/apifox/${this.apifoxPreview.source_preview.id}/activate`, {},
        )
        this.activeRevision = response.data.source_revision
        this.environment = response.data.environment
        this.secretPlaceholders = [...response.data.secret_placeholders]
        this.apifoxPreview = null
        this.preview = null
        this.message = `接口 v${this.activeRevision.revision_number} 与环境 v${this.environment.revision} 已保存`
        return response.data
      } catch (error) {
        this.error = error instanceof Error ? error.message : 'Apifox 更新保存失败'
        throw error
      } finally {
        this.busy = false
      }
    },
    async createProject(name: string): Promise<string> {
      this.error = ''
      const slug = `${slugify(name) || 'api-project'}-${Date.now().toString(36)}`
      const response = await apiClient.post<{ project: { id: string } }>('/api/api-testing/v1/projects', {
        name: name.trim(), slug, description: '',
      })
      return response.data.project.id
    },
    async previewSource(projectId: string, sourceId: string | null, document: Record<string, unknown>): Promise<SourcePreview> {
      this.busy = true
      this.error = ''
      this.message = ''
      try {
        const response = await apiClient.post<{ preview: SourcePreview }>('/api/api-testing/v1/sources/preview', {
          project_id: projectId, source_id: sourceId, document,
        })
        this.preview = response.data.preview
        this.message = '已读取接口变化，确认后保存为新版本'
        return this.preview
      } catch (error) {
        this.error = error instanceof Error ? error.message : '接口文件读取失败'
        throw error
      } finally {
        this.busy = false
      }
    },
    async activatePreview(): Promise<SourceRevision> {
      if (!this.preview) throw new Error('请先读取接口文件')
      this.busy = true
      this.error = ''
      try {
        const response = await apiClient.post<{ source_revision: SourceRevision }>(
          `/api/api-testing/v1/sources/${this.preview.id}/activate`, {},
        )
        this.activeRevision = response.data.source_revision
        this.message = `接口版本 v${this.activeRevision.revision_number} 已保存`
        this.preview = null
        return this.activeRevision
      } catch (error) {
        this.error = error instanceof Error ? error.message : '接口版本保存失败'
        throw error
      } finally {
        this.busy = false
      }
    },
    async loadEnvironmentRevision(revisionId: string): Promise<EnvironmentView> {
      const response = await apiClient.get<{ environment_revision: EnvironmentView }>(
        `/api/api-testing/v1/environment-revisions/${encodeURIComponent(revisionId)}`,
      )
      this.environment = response.data.environment_revision
      return this.environment
    },
    async saveEnvironment(environmentId: string | null, payload: EnvironmentPayload): Promise<EnvironmentView> {
      this.busy = true
      this.error = ''
      this.message = ''
      const secretUpdates = Object.fromEntries(
        Object.entries(this.secretUpdates).filter(([, value]) => value.length > 0),
      )
      try {
        let environment: EnvironmentView
        if (environmentId) {
          const response = await apiClient.post<{ environment: EnvironmentView }>(
            `/api/api-testing/v1/environments/${environmentId}/revisions`,
            { environment: editableEnvironment(payload), secret_updates: secretUpdates },
          )
          environment = response.data.environment
        } else {
          const created = await apiClient.post<{ environment: EnvironmentView }>(
            '/api/api-testing/v1/environments/import', payload,
          )
          environment = created.data.environment
          if (Object.keys(secretUpdates).length) {
            const secured = await apiClient.post<{ environment: EnvironmentView }>(
              `/api/api-testing/v1/environments/${environment.id}/revisions`,
              { environment: {}, secret_updates: secretUpdates },
            )
            environment = secured.data.environment
          }
        }
        this.environment = environment
        this.message = `环境 ${environment.name} · v${environment.revision} 已保存`
        return environment
      } catch (error) {
        this.error = error instanceof Error ? error.message : '环境保存失败'
        throw error
      } finally {
        this.secretUpdates = {}
        this.busy = false
      }
    },
  },
})

function editableEnvironment(payload: EnvironmentPayload): Record<string, unknown> {
  return {
    name: payload.name,
    description: payload.description || '',
    services: payload.services,
    variables: payload.variables,
    default_headers: payload.default_headers,
  }
}

function slugify(value: string): string {
  return value.trim().toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '')
}
