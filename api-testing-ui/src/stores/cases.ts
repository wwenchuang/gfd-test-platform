import { defineStore } from 'pinia'

import { apiClient } from '../api/client'
import type { AiJob, ApiEndpoint, CaseDraft, CaseVersion, DebugResult, ExecutionView } from '../api/contracts'

const TERMINAL_AI = new Set(['completed', 'partial', 'failed', 'failed_gateway', 'failed_validation'])
const TERMINAL_EXECUTION = new Set(['DONE', 'CANCELLED', 'PASSED', 'FAILED', 'BROKEN'])

export const useCasesStore = defineStore('api-cases', {
  state: () => ({
    drafts: {} as Record<string, CaseDraft>,
    versions: {} as Record<string, CaseVersion>,
    versionByEndpoint: {} as Record<string, string>,
    aiJob: null as AiJob | null,
    aiError: '',
    saving: false,
    savedMessage: '',
    debugExecution: null as ExecutionView | null,
    debugResult: null as DebugResult | null,
  }),
  actions: {
    draftFor(endpoint: ApiEndpoint): CaseDraft {
      if (!this.drafts[endpoint.id]) this.drafts[endpoint.id] = blankDraft(endpoint)
      return this.drafts[endpoint.id]
    },
    updateDraft(endpointId: string, draft: CaseDraft): void {
      this.drafts[endpointId] = structuredClone(draft)
      this.savedMessage = ''
    },
    async save(endpointId: string): Promise<CaseVersion> {
      const draft = this.drafts[endpointId]
      if (!draft) throw new Error('请先编辑测试用例')
      this.saving = true
      this.savedMessage = ''
      try {
        const existingId = this.versionByEndpoint[endpointId]
        const existing = existingId ? this.versions[existingId] : null
        const path = existing
          ? `/api/api-testing/v1/cases/${existing.case_id}/versions`
          : '/api/api-testing/v1/cases'
        const body = existing ? { case: draft } : { endpoint_id: endpointId, case: draft, origin: 'manual' }
        const response = await apiClient.post<{ case_version: CaseVersion }>(path, body)
        const version = response.data.case_version
        this.versions[version.id] = version
        this.versionByEndpoint[endpointId] = version.id
        this.drafts[endpointId] = fromVersion(version)
        this.savedMessage = `草稿 v${version.version} 已保存`
        return version
      } finally {
        this.saving = false
      }
    },
    async generate(endpointIds: string[], environmentRevisionId: string, intent: string): Promise<void> {
      this.aiError = ''
      try {
        const response = await apiClient.post<{ job: AiJob }>('/api/api-testing/v1/ai-jobs', {
          endpoint_ids: endpointIds, environment_revision_id: environmentRevisionId, intent,
        })
        this.aiJob = response.data.job
        await this.pollAiJob(response.data.job.id)
      } catch (error) {
        this.aiError = error instanceof Error ? error.message : 'AI 生成失败'
      }
    },
    async pollAiJob(jobId: string): Promise<void> {
      for (let attempt = 0; attempt < 120; attempt += 1) {
        const response = await apiClient.get<{ job: AiJob }>(`/api/api-testing/v1/ai-jobs/${jobId}`)
        this.aiJob = response.data.job
        if (TERMINAL_AI.has(this.aiJob.state)) {
          for (const batch of this.aiJob.batches) {
            for (const versionId of batch.generated_draft_ids) await this.loadVersion(versionId)
          }
          return
        }
        await delay(1500)
      }
      this.aiError = 'AI 生成仍在运行，可稍后继续查看'
    },
    async loadVersion(versionId: string): Promise<void> {
      const response = await apiClient.get<{ case_version: CaseVersion }>(`/api/api-testing/v1/case-versions/${versionId}`)
      const version = response.data.case_version
      this.versions[version.id] = version
      this.versionByEndpoint[version.endpoint_id] = version.id
      this.drafts[version.endpoint_id] = fromVersion(version)
    },
    async debug(input: { projectId: string; sourceRevisionId: string; environmentRevisionId: string; caseVersionId: string }): Promise<void> {
      this.debugResult = null
      const response = await apiClient.post<{ execution: ExecutionView }>('/api/api-testing/v1/executions', {
        project_id: input.projectId,
        source_revision_id: input.sourceRevisionId,
        environment_revision_id: input.environmentRevisionId,
        case_version_ids: [input.caseVersionId],
        execution_type: 'debug',
        overrides: {},
        idempotency_key: crypto.randomUUID(),
      })
      this.debugExecution = response.data.execution
      await this.pollExecution(response.data.execution.id)
    },
    async pollExecution(executionId: string): Promise<void> {
      for (let attempt = 0; attempt < 240; attempt += 1) {
        const response = await apiClient.get<{ execution: ExecutionView }>(`/api/api-testing/v1/executions/${executionId}`)
        this.debugExecution = response.data.execution
        if (TERMINAL_EXECUTION.has(this.debugExecution.state)) {
          const result = this.debugExecution.case_results[0]
          if (result) this.debugResult = toDebugResult(result)
          return
        }
        await delay(1000)
      }
    },
    async adoptBaseline(caseVersionId: string, executionCaseId: string): Promise<void> {
      await apiClient.post(`/api/api-testing/v1/case-versions/${caseVersionId}/baseline`, {
        debug_execution_case_id: executionCaseId,
      })
    },
  },
})

function blankDraft(endpoint: ApiEndpoint): CaseDraft {
  return {
    name: endpoint.summary || `${endpoint.method} ${endpoint.path}`,
    purpose: `验证${endpoint.summary || endpoint.path}`,
    priority: 'P1',
    request: { method: endpoint.method, path: endpoint.path, service: 'default', path_params: {}, query: {}, headers: {}, cookies: {}, body: null },
    data_rows: [], assertions: [{ type: 'status_code', operator: 'equals', expected: 200, timeout_ms: 0, enabled: true }],
    extractions: [], dependencies: [], processing: { pre: [], post: [] },
  }
}

function fromVersion(version: CaseVersion): CaseDraft {
  return structuredClone({
    name: version.name,
    purpose: version.purpose,
    priority: version.priority,
    request: version.request,
    data_rows: version.data_rows,
    assertions: version.assertions,
    extractions: version.extractions,
    dependencies: version.dependencies,
    processing: version.processing,
  })
}

function toDebugResult(value: ExecutionView['case_results'][number]): DebugResult {
  const result = value.sanitized_result
  return {
    status: value.status,
    executionCaseId: value.execution_case_id,
    resolvedRequest: (result.sanitized_request || result.request || {}) as Record<string, unknown>,
    sanitizedResponse: (result.sanitized_response || result.response || {}) as Record<string, unknown>,
    assertions: (result.assertions || result.assertion_results || []) as unknown[],
    failureCategory: value.failure_category,
    logs: [`状态：${value.status}`, `耗时：${value.duration_ms} ms`],
  }
}

function delay(milliseconds: number): Promise<void> {
  return new Promise(resolve => window.setTimeout(resolve, milliseconds))
}
