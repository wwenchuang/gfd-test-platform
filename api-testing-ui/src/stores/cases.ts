import { defineStore } from 'pinia'

import { apiClient } from '../api/client'
import type { AiJob, ApiEndpoint, CaseDraft, CaseValidation, CaseVersion, DebugResult, EnvironmentRevisionSnapshot, ExecutionView, GeneratedCasePreview } from '../api/contracts'
import { validateCaseDraftLocally } from '../utils/caseDraftValidation'
import { createIdempotencyKey } from '../utils/idempotency'

const TERMINAL_AI = new Set(['completed', 'partial', 'failed', 'failed_gateway', 'failed_validation'])
const TERMINAL_EXECUTION = new Set(['DONE', 'CANCELLED', 'PASSED', 'FAILED', 'BROKEN'])

export const useCasesStore = defineStore('api-cases', {
  state: () => ({
    drafts: {} as Record<string, CaseDraft>,
    versions: {} as Record<string, CaseVersion>,
    versionIdsByEndpoint: {} as Record<string, string[]>,
    activeVersionByEndpoint: {} as Record<string, string>,
    generatedPreviews: [] as GeneratedCasePreview[],
    activeGeneratedPreviewId: '',
    aiJob: null as AiJob | null,
    aiError: '',
    aiPolling: false,
    aiCanResume: false,
    lastAiJobId: '',
    basicGenerating: false,
    saving: false,
    savedMessage: '',
    validationErrors: {} as Record<string, string>,
    validationWarnings: {} as Record<string, string>,
    debugExecution: null as ExecutionView | null,
    debugResult: null as DebugResult | null,
    debugPolling: false,
    debugCanResume: false,
    debugError: '',
    debugGeneration: 0,
    baselineAdopting: false,
    baselineMessage: '',
    baselineError: '',
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
    setActiveVersion(endpointId: string, versionId: string): void {
      if (!this.versionIdsByEndpoint[endpointId]?.includes(versionId)) return
      this.activeVersionByEndpoint[endpointId] = versionId
      this.activeGeneratedPreviewId = ''
      const version = this.versions[versionId]
      if (version) this.drafts[endpointId] = fromVersion(version)
      this.validationErrors = {}
      this.validationWarnings = {}
      this.savedMessage = ''
      this.clearBaselineFeedback()
    },
    registerVersion(version: CaseVersion, makeActive = true): void {
      this.versions[version.id] = version
      const ids = this.versionIdsByEndpoint[version.endpoint_id] || []
      const previousId = ids.find(id => this.versions[id]?.case_id === version.case_id)
      const nextIds = previousId
        ? ids.map(id => id === previousId ? version.id : id)
        : [...ids, version.id]
      this.versionIdsByEndpoint[version.endpoint_id] = [...new Set(nextIds)]
      if (previousId && previousId !== version.id) delete this.versions[previousId]
      if (makeActive || !this.activeVersionByEndpoint[version.endpoint_id]) {
        this.activeVersionByEndpoint[version.endpoint_id] = version.id
        this.drafts[version.endpoint_id] = fromVersion(version)
      }
    },
    async loadSavedCases(sourceRevisionId: string): Promise<void> {
      const response = await apiClient.get<{ case_versions: CaseVersion[] }>(
        `/api/api-testing/v1/cases?source_revision_id=${encodeURIComponent(sourceRevisionId)}`,
      )
      this.versions = {}
      this.versionIdsByEndpoint = {}
      this.activeVersionByEndpoint = {}
      this.generatedPreviews = []
      this.activeGeneratedPreviewId = ''
      for (const version of response.data.case_versions) this.registerVersion(version, false)
    },
    async save(endpointId: string, environmentRevisionId?: string): Promise<CaseVersion> {
      const draft = this.drafts[endpointId]
      if (!draft) throw new Error('请先编辑测试用例')
      this.saving = true
      this.savedMessage = ''
      try {
        const existingId = this.activeVersionByEndpoint[endpointId]
        const existing = existingId ? this.versions[existingId] : null
        const path = existing
          ? `/api/api-testing/v1/cases/${existing.case_id}/versions`
          : '/api/api-testing/v1/cases'
        const body = existing ? { case: draft } : { endpoint_id: endpointId, case: draft, origin: 'manual' }
        const response = await apiClient.post<{ case_version: CaseVersion }>(path, body)
        const version = response.data.case_version
        this.registerVersion(version)
        this.savedMessage = `草稿 v${version.version} 已保存`
        await this.validate(version.id, environmentRevisionId)
        return version
      } finally {
        this.saving = false
      }
    },
    async saveForDebug(endpointId: string, environmentRevisionId: string): Promise<CaseVersion> {
      const draft = this.drafts[endpointId]
      if (!draft) throw new Error('请先编辑测试用例')
      const localErrors = validateCaseDraftLocally(draft)
      if (Object.keys(localErrors).length) {
        this.validationErrors = localErrors
        this.validationWarnings = {}
        throw new Error(Object.values(localErrors)[0])
      }
      const version = await this.save(endpointId, environmentRevisionId)
      const firstError = Object.values(this.validationErrors)[0]
      if (firstError) throw new Error(`请先修正用例校验错误：${firstError}`)
      return version
    },
    async generate(endpointIds: string[], environmentRevisionId: string, intent: string, taskId?: string): Promise<void> {
      this.aiError = ''
      this.aiCanResume = false
      try {
        const response = await apiClient.post<{ job: AiJob }>('/api/api-testing/v1/ai-jobs', {
          endpoint_ids: endpointIds, environment_revision_id: environmentRevisionId, intent,
          ...(taskId ? { task_id: taskId } : {}),
        })
        this.aiJob = response.data.job
        this.lastAiJobId = response.data.job.id
        await this.pollAiJob(response.data.job.id)
      } catch (error) {
        this.aiError = error instanceof Error ? error.message : 'AI 生成失败'
      }
    },
    async generateBasicPositive(endpointIds: string[], environmentRevisionId: string, taskId?: string): Promise<CaseVersion[]> {
      this.basicGenerating = true
      this.savedMessage = ''
      try {
        const response = await apiClient.post<{ case_versions: CaseVersion[] }>('/api/api-testing/v1/cases/basic-positive', {
          endpoint_ids: endpointIds,
          environment_revision_id: environmentRevisionId,
          ...(taskId ? { task_id: taskId } : {}),
        })
        const versions = response.data.case_versions
        for (const version of versions) this.registerVersion(version)
        this.savedMessage = `已生成 ${versions.length} 个基础正向用例`
        return versions
      } finally {
        this.basicGenerating = false
      }
    },
    async previewBasicPositive(endpointIds: string[], environmentRevisionId: string, taskId?: string): Promise<GeneratedCasePreview[]> {
      this.basicGenerating = true
      this.savedMessage = ''
      try {
        const response = await apiClient.post<{ case_previews: GeneratedCasePreview[] }>('/api/api-testing/v1/cases/basic-positive/preview', {
          endpoint_ids: endpointIds,
          environment_revision_id: environmentRevisionId,
          ...(taskId ? { task_id: taskId } : {}),
        })
        const previews = response.data.case_previews
        const incomingIds = new Set(previews.map(item => item.id))
        const incomingEndpointIds = new Set(previews.map(item => item.endpoint_id))
        this.generatedPreviews = [
          ...this.generatedPreviews.filter(item => !incomingIds.has(item.id) && !incomingEndpointIds.has(item.endpoint_id)),
          ...previews.map(item => cloneJson(item)),
        ]
        this.savedMessage = `已生成 ${previews.length} 个基础正向候选，请确认后保存`
        return previews
      } finally {
        this.basicGenerating = false
      }
    },
    setDraftFromGeneratedPreview(previewId: string): void {
      const preview = this.generatedPreviews.find(item => item.id === previewId)
      if (!preview) return
      this.drafts[preview.endpoint_id] = cloneJson(preview.case)
      delete this.activeVersionByEndpoint[preview.endpoint_id]
      this.activeGeneratedPreviewId = preview.id
      this.validationErrors = {}
      this.validationWarnings = {}
      this.savedMessage = ''
      this.clearDebug()
      this.clearBaselineFeedback()
    },
    async saveGeneratedPreview(previewId: string, draft?: CaseDraft): Promise<CaseVersion> {
      const preview = this.generatedPreviews.find(item => item.id === previewId)
      if (!preview) throw new Error('请选择要保存的基础正向候选')
      this.saving = true
      this.savedMessage = ''
      try {
        const caseDraft = cloneJson(draft || preview.case)
        const response = await apiClient.post<{ case_version: CaseVersion }>('/api/api-testing/v1/cases', {
          endpoint_id: preview.endpoint_id,
          case: caseDraft,
          origin: preview.origin || 'imported',
        })
        const version = response.data.case_version
        this.registerVersion(version)
        this.discardGeneratedPreview(preview.id)
        this.savedMessage = '基础正向用例已保存'
        return version
      } finally {
        this.saving = false
      }
    },
    async saveAllGeneratedPreviews(draftOverrides: Record<string, CaseDraft> = {}): Promise<CaseVersion[]> {
      const previews = [...this.generatedPreviews]
      const versions: CaseVersion[] = []
      for (const preview of previews) {
        versions.push(await this.saveGeneratedPreview(preview.id, draftOverrides[preview.id]))
      }
      this.savedMessage = `已保存 ${versions.length} 个基础正向用例`
      return versions
    },
    async updateVersionGroup(versionId: string, groupName: string): Promise<CaseVersion> {
      this.saving = true
      this.savedMessage = ''
      try {
        const response = await apiClient.put<{ case_version: CaseVersion }>(
          `/api/api-testing/v1/case-versions/${versionId}/group`,
          { group_name: groupName },
        )
        const version = response.data.case_version
        this.registerVersion(version, false)
        this.savedMessage = `已移动到分组“${version.group_name}”`
        return version
      } finally {
        this.saving = false
      }
    },
    async updateVersionGroups(versionIds: string[], groupName: string): Promise<CaseVersion[]> {
      const uniqueIds = [...new Set(versionIds)]
      if (!uniqueIds.length) return []
      this.saving = true
      this.savedMessage = ''
      const updated: CaseVersion[] = []
      try {
        for (const versionId of uniqueIds) {
          try {
            const response = await apiClient.put<{ case_version: CaseVersion }>(
              `/api/api-testing/v1/case-versions/${versionId}/group`,
              { group_name: groupName },
            )
            updated.push(response.data.case_version)
            this.registerVersion(response.data.case_version, false)
          } catch {
            this.savedMessage = `已移动 ${updated.length}/${uniqueIds.length} 条用例，剩余用例未修改`
            throw new Error(`批量移动未完成：已完成 ${updated.length}/${uniqueIds.length}，失败用例 ${versionId}`)
          }
        }
        this.savedMessage = `已将 ${updated.length} 条用例移动到分组“${groupName}”`
        return updated
      } finally {
        this.saving = false
      }
    },
    discardGeneratedPreview(previewId: string): void {
      const preview = this.generatedPreviews.find(item => item.id === previewId)
      this.generatedPreviews = this.generatedPreviews.filter(item => item.id !== previewId)
      if (this.activeGeneratedPreviewId === previewId) {
        this.activeGeneratedPreviewId = ''
        if (preview && !this.activeVersionByEndpoint[preview.endpoint_id]) {
          delete this.drafts[preview.endpoint_id]
        }
      }
    },
    async pollAiJob(jobId: string, options: { maxAttempts?: number; delayMs?: number } = {}): Promise<void> {
      const maxAttempts = options.maxAttempts ?? 120
      const delayMs = options.delayMs ?? 1500
      this.aiPolling = true
      this.aiCanResume = false
      this.lastAiJobId = jobId
      try {
        for (let attempt = 0; attempt < maxAttempts; attempt += 1) {
          const response = await apiClient.get<{ job: AiJob }>(`/api/api-testing/v1/ai-jobs/${jobId}`)
          this.aiJob = response.data.job
          if (TERMINAL_AI.has(this.aiJob.state)) {
            for (const batch of this.aiJob.batches) {
              for (const versionId of batch.generated_draft_ids) await this.loadVersion(versionId)
            }
            return
          }
          if (delayMs > 0) await delay(delayMs)
        }
        this.aiCanResume = true
        this.aiError = 'AI 仍在后台生成，点击“继续查看”即可恢复进度'
      } catch (error) {
        this.aiCanResume = true
        this.aiError = error instanceof Error ? `${error.message}，可继续查看任务` : 'AI 进度读取失败，可继续查看任务'
      } finally {
        this.aiPolling = false
      }
    },
    async resumeAiJob(): Promise<void> {
      if (!this.lastAiJobId || this.aiPolling) return
      this.aiError = ''
      await this.pollAiJob(this.lastAiJobId)
    },
    clearAiJob(): void {
      this.aiJob = null
      this.aiError = ''
      this.aiPolling = false
      this.aiCanResume = false
      this.lastAiJobId = ''
    },
    async restoreLatestAiJob(projectId: string): Promise<void> {
      try {
        const response = await apiClient.get<{ job: AiJob | null }>(
          `/api/api-testing/v1/ai-jobs/latest?project_id=${encodeURIComponent(projectId)}`,
        )
        const job = response.data.job
        if (!job || TERMINAL_AI.has(job.state)) return
        this.aiJob = job
        this.lastAiJobId = job.id
        this.aiCanResume = true
        this.aiError = '发现后台生成任务，点击“继续查看”恢复进度'
      } catch {
        // Workspace startup remains usable when no prior AI job can be restored.
      }
    },
    async loadVersion(versionId: string): Promise<void> {
      const response = await apiClient.get<{ case_version: CaseVersion }>(`/api/api-testing/v1/case-versions/${versionId}`)
      const version = response.data.case_version
      this.registerVersion(version)
    },
    async archiveCase(endpointId: string, versionId: string): Promise<void> {
      const version = this.versions[versionId]
      if (!version) throw new Error('请选择要删除的用例')
      await apiClient.delete(`/api/api-testing/v1/cases/${version.case_id}`)
      for (const [id, item] of Object.entries(this.versions)) {
        if (item.case_id === version.case_id) delete this.versions[id]
      }
      const remainingIds = (this.versionIdsByEndpoint[endpointId] || [])
        .filter(id => this.versions[id] && this.versions[id].case_id !== version.case_id)
      this.versionIdsByEndpoint[endpointId] = remainingIds
      if (this.activeVersionByEndpoint[endpointId] === versionId) {
        if (remainingIds.length) this.setActiveVersion(endpointId, remainingIds[0])
        else {
          delete this.activeVersionByEndpoint[endpointId]
          delete this.drafts[endpointId]
        }
      }
      this.clearDebug()
      this.validationErrors = {}
      this.validationWarnings = {}
      this.savedMessage = '用例已删除，历史执行和基线证据仍保留'
    },
    async validate(versionId: string, environmentRevisionId?: string): Promise<void> {
      this.validationErrors = {}
      this.validationWarnings = {}
      let environmentMetadata: Record<string, unknown> = {}
      if (environmentRevisionId) {
        const environmentResponse = await apiClient.get<{ environment_revision: EnvironmentRevisionSnapshot }>(
          `/api/api-testing/v1/environment-revisions/${environmentRevisionId}`,
        )
        const snapshot = environmentResponse.data.environment_revision
        environmentMetadata = {
          variables: snapshot.variables,
          services: snapshot.services,
          headers: Object.fromEntries(
            Object.keys(snapshot.default_headers || {}).map(name => [name, { configured: true }]),
          ),
        }
      }
      const response = await apiClient.post<{ validation: CaseValidation }>(`/api/api-testing/v1/case-versions/${versionId}/validate`, {
        environment_metadata: environmentMetadata,
      })
      const validation = response.data.validation
      if (!validation) return
      this.validationErrors = issueMap(validation.errors || [])
      this.validationWarnings = issueMap(validation.warnings || [])
    },
    async debug(input: { projectId: string; sourceRevisionId: string; environmentRevisionId: string; caseVersionId: string; taskId?: string }): Promise<void> {
      this.debugGeneration += 1
      this.debugResult = null
      this.debugError = ''
      this.debugCanResume = false
      this.clearBaselineFeedback()
      const response = await apiClient.post<{ execution: ExecutionView }>('/api/api-testing/v1/executions', {
        project_id: input.projectId,
        source_revision_id: input.sourceRevisionId,
        environment_revision_id: input.environmentRevisionId,
        case_version_ids: [input.caseVersionId],
        execution_type: 'debug',
        overrides: {},
        idempotency_key: createIdempotencyKey(),
        ...(input.taskId ? { task_id: input.taskId } : {}),
      })
      this.debugExecution = response.data.execution
      await this.pollExecution(response.data.execution.id)
    },
    async pollExecution(executionId: string, options: { maxAttempts?: number; delayMs?: number } = {}): Promise<void> {
      const maxAttempts = options.maxAttempts ?? 240
      const delayMs = options.delayMs ?? 1000
      const generation = this.debugGeneration
      this.debugPolling = true
      this.debugCanResume = false
      this.debugError = ''
      try {
        for (let attempt = 0; attempt < maxAttempts; attempt += 1) {
          const response = await apiClient.get<{ execution: ExecutionView }>(`/api/api-testing/v1/executions/${executionId}`)
          if (generation !== this.debugGeneration) return
          this.debugExecution = response.data.execution
          if (TERMINAL_EXECUTION.has(this.debugExecution.state)) {
            const result = this.debugExecution.case_results.find(
              item => item.execution_role === 'requested',
            ) || this.debugExecution.case_results[0]
            if (result) this.debugResult = toDebugResult(result)
            this.debugCanResume = false
            return
          }
          if (delayMs > 0) await delay(delayMs)
        }
        this.debugCanResume = true
        this.debugError = '调试仍在后台执行，可关闭抽屉后从执行记录继续查看'
      } catch (error) {
        this.debugCanResume = true
        this.debugError = error instanceof Error ? `${error.message}，可继续查看进度` : '调试进度读取失败，可继续查看进度'
      } finally {
        if (generation === this.debugGeneration) this.debugPolling = false
      }
    },
    async resumeDebug(): Promise<void> {
      if (!this.debugExecution?.id || this.debugPolling) return
      this.debugError = ''
      await this.pollExecution(this.debugExecution.id)
    },
    clearDebug(): void {
      this.debugGeneration += 1
      this.debugExecution = null
      this.debugResult = null
      this.debugPolling = false
      this.debugCanResume = false
      this.debugError = ''
      this.clearBaselineFeedback()
    },
    async adoptBaseline(caseVersionId: string, executionCaseId: string): Promise<void> {
      if (this.baselineAdopting) return
      this.baselineAdopting = true
      this.baselineMessage = ''
      this.baselineError = ''
      try {
        await apiClient.post(`/api/api-testing/v1/case-versions/${caseVersionId}/baseline`, {
          debug_execution_case_id: executionCaseId,
        })
        this.baselineMessage = '已采纳为基线'
      } catch (error) {
        this.baselineError = error instanceof Error ? error.message : '采纳基线失败'
      } finally {
        this.baselineAdopting = false
      }
    },
    clearBaselineFeedback(): void {
      this.baselineAdopting = false
      this.baselineMessage = ''
      this.baselineError = ''
    },
  },
})

function blankDraft(endpoint: ApiEndpoint): CaseDraft {
  return {
    name: endpoint.summary || `${endpoint.method} ${endpoint.path}`,
    purpose: `验证${endpoint.summary || endpoint.path}`,
    app_package: '',
    app_name: '',
    business: '',
    priority: 'P1',
    request: { method: endpoint.method, path: endpoint.path, service: 'default', ...requestParameterExamples(endpoint.operation), headers: {}, body: requestBodyExample(endpoint.operation) },
    data_rows: [], assertions: [{ type: 'status_code', operator: 'equals', expected: 200, timeout_ms: 0, enabled: true }],
    extractions: [], dependencies: [], processing: { pre: [], post: [], setup_steps: [], cleanup_steps: [] },
  }
}

function requestParameterExamples(operation: Record<string, unknown> | undefined): Pick<CaseDraft['request'], 'path_params' | 'query' | 'cookies'> {
  const result = {
    path_params: {} as Record<string, unknown>,
    query: {} as Record<string, unknown>,
    cookies: {} as Record<string, unknown>,
  }
  if (!operation) return result
  const parameters = [
    ...arrayRecords(operation.path_parameters),
    ...arrayRecords(operation.parameters),
  ]
  for (const parameter of parameters) {
    const name = typeof parameter.name === 'string' ? parameter.name.trim() : ''
    const location = typeof parameter.in === 'string' ? parameter.in : ''
    if (!name || location === 'header') continue
    const value = parameterExample(parameter, operation)
    if (value === undefined) continue
    if (location === 'path') result.path_params[name] = value
    else if (location === 'query') result.query[name] = value
    else if (location === 'cookie') result.cookies[name] = value
  }
  return result
}

function parameterExample(parameter: Record<string, unknown>, operation: Record<string, unknown>): unknown {
  if (Object.prototype.hasOwnProperty.call(parameter, 'example')) return cloneJson(parameter.example)
  const examples = asRecord(parameter.examples)
  if (examples) {
    for (const item of Object.values(examples)) {
      const example = asRecord(item)
      if (example && Object.prototype.hasOwnProperty.call(example, 'value')) return cloneJson(example.value)
    }
  }
  const schema = resolveOperationSchema(asRecord(parameter.schema), operation)
  if (schema && Object.prototype.hasOwnProperty.call(schema, 'example')) return cloneJson(schema.example)
  if (schema && Object.prototype.hasOwnProperty.call(schema, 'default')) return cloneJson(schema.default)
  return undefined
}

function requestBodyExample(operation: Record<string, unknown> | undefined): unknown {
  if (!operation) return null
  const requestBody = resolveOperationReference(asRecord(operation.requestBody), operation)
  const content = asRecord(requestBody?.content)
  if (!content) return null
  const mediaEntries = Object.entries(content)
  const preferred = mediaEntries.find(([name]) => name === 'application/json')
    || mediaEntries.find(([name]) => name.endsWith('+json'))
    || mediaEntries[0]
  const media = asRecord(preferred?.[1])
  if (!media) return null
  if (Object.prototype.hasOwnProperty.call(media, 'example')) return cloneJson(media.example)
  const examples = asRecord(media.examples)
  if (examples) {
    for (const item of Object.values(examples)) {
      const example = asRecord(item)
      if (example && Object.prototype.hasOwnProperty.call(example, 'value')) return cloneJson(example.value)
    }
  }
  const schema = resolveOperationSchema(asRecord(media.schema), operation)
  if (schema && Object.prototype.hasOwnProperty.call(schema, 'example')) return cloneJson(schema.example)
  if (schema && Object.prototype.hasOwnProperty.call(schema, 'default')) return cloneJson(schema.default)
  const body = objectFromSchemaExamples(schema, operation)
  if (body !== undefined) return body
  return null
}

function objectFromSchemaExamples(
  schema: Record<string, unknown> | null,
  operation: Record<string, unknown>,
): Record<string, unknown> | undefined {
  const resolved = resolveOperationSchema(schema, operation)
  const properties = asRecord(resolved?.properties)
  if (!properties) return undefined
  const body: Record<string, unknown> = {}
  for (const [name, property] of Object.entries(properties)) {
    const propertySchema = resolveOperationSchema(asRecord(property), operation)
    if (!propertySchema) continue
    if (Object.prototype.hasOwnProperty.call(propertySchema, 'example')) body[name] = cloneJson(propertySchema.example)
    else if (Object.prototype.hasOwnProperty.call(propertySchema, 'default')) body[name] = cloneJson(propertySchema.default)
  }
  return Object.keys(body).length ? body : undefined
}

function resolveOperationSchema(
  schema: Record<string, unknown> | null,
  operation: Record<string, unknown>,
): Record<string, unknown> | null {
  return resolveOperationReference(schema, operation)
}

function resolveOperationReference(
  value: Record<string, unknown> | null,
  operation: Record<string, unknown>,
): Record<string, unknown> | null {
  let current = value
  const dependencies = asRecord(operation.resolved_dependencies)
  const seen = new Set<string>()
  while (current && typeof current.$ref === 'string' && dependencies && !seen.has(current.$ref)) {
    seen.add(current.$ref)
    current = asRecord(dependencies[current.$ref])
  }
  return current
}

function asRecord(value: unknown): Record<string, unknown> | null {
  return value && typeof value === 'object' && !Array.isArray(value)
    ? value as Record<string, unknown>
    : null
}

function arrayRecords(value: unknown): Record<string, unknown>[] {
  return Array.isArray(value)
    ? value.filter((item): item is Record<string, unknown> => Boolean(asRecord(item)))
    : []
}

function fromVersion(version: CaseVersion): CaseDraft {
  return cloneJson({
    name: version.name,
    purpose: version.purpose,
    app_package: version.app_package || '',
    app_name: version.app_name || '',
    business: version.business || '',
    priority: version.priority,
    request: version.request,
    data_rows: version.data_rows.map(row => ({ name: row.name, values: row.values, enabled: row.enabled })),
    assertions: version.assertions.map(publicAssertion),
    extractions: version.extractions.map(publicExtraction),
    dependencies: version.dependencies,
    processing: {
      pre: version.processing?.pre || [],
      post: version.processing?.post || [],
      setup_steps: version.processing?.setup_steps || [],
      cleanup_steps: version.processing?.cleanup_steps || [],
    },
  })
}

function cloneJson<T>(value: T): T {
  return JSON.parse(JSON.stringify(value)) as T
}

function publicAssertion(assertion: Record<string, unknown>): Record<string, unknown> {
  const result: Record<string, unknown> = {
    type: assertion.type,
    operator: assertion.operator,
    expected: assertion.expected,
    timeout_ms: assertion.timeout_ms ?? 0,
    enabled: assertion.enabled ?? true,
  }
  if (typeof assertion.path === 'string' && assertion.path) result.path = assertion.path
  if (typeof assertion.name === 'string' && assertion.name) result.name = assertion.name
  return result
}

function publicExtraction(extraction: Record<string, unknown>): Record<string, unknown> {
  const result: Record<string, unknown> = {
    target: extraction.target,
    type: extraction.type,
    required: extraction.required ?? true,
  }
  if (typeof extraction.path === 'string' && extraction.path) result.path = extraction.path
  if (typeof extraction.name === 'string' && extraction.name) result.name = extraction.name
  if (Object.prototype.hasOwnProperty.call(extraction, 'default')) result.default = extraction.default
  return result
}

function toDebugResult(value: ExecutionView['case_results'][number]): DebugResult {
  const result = value.sanitized_result
  const trace = Array.isArray(result.trace) ? result.trace : []
  const logs = trace.map((item) => {
    if (!item || typeof item !== 'object') return String(item)
    const row = item as Record<string, unknown>
    return [row.phase, row.message || row.error_message || row.status].filter(Boolean).join(' · ')
  }).filter(Boolean)
  if (typeof result.error_message === 'string' && result.error_message) logs.push(`错误 · ${result.error_message}`)
  logs.unshift(`状态 · ${value.status}`, `耗时 · ${value.duration_ms} ms`)
  return {
    status: value.status,
    executionCaseId: value.execution_case_id,
    durationMs: value.duration_ms,
    errorMessage: typeof result.error_message === 'string' ? result.error_message : '',
    trace: trace.flatMap(item => {
      const row = asRecord(item)
      if (!row || row.phase !== 'workflow_step' || !['setup', 'main', 'cleanup'].includes(String(row.stage))) return []
      const extracted = asRecord(row.extracted_variables)
      return [{
        stage: row.stage as 'setup' | 'main' | 'cleanup',
        index: typeof row.index === 'number' ? row.index : 0,
        name: typeof row.name === 'string' ? row.name : '未命名步骤',
        status: typeof row.status === 'string' ? row.status : 'UNKNOWN',
        failureCategory: typeof row.failure_category === 'string' ? row.failure_category : '',
        assertions: Array.isArray(row.assertions) ? row.assertions : [],
        extractedVariableNames: extracted ? Object.keys(extracted) : [],
        missingVariableNames: Array.isArray(row.missing_variables) ? row.missing_variables.filter((name): name is string => typeof name === 'string') : [],
        request: asRecord(row.request) || {},
        response: asRecord(row.response) || {},
        error: typeof row.error_message === 'string' ? row.error_message : '',
        attempt: typeof row.attempt === 'number' ? row.attempt : 1,
        maxAttempts: typeof row.max_attempts === 'number' ? row.max_attempts : 1,
      }]
    }),
    resolvedRequest: (result.sanitized_request || result.request || {}) as Record<string, unknown>,
    sanitizedResponse: (result.sanitized_response || result.response || {}) as Record<string, unknown>,
    assertions: (result.assertions || result.assertion_results || []) as unknown[],
    failureCategory: value.failure_category,
    logs,
  }
}

function delay(milliseconds: number): Promise<void> {
  return new Promise(resolve => globalThis.setTimeout(resolve, milliseconds))
}

function issueMap(issues: Array<{ field: string; message: string }>): Record<string, string> {
  return Object.fromEntries(issues.map(issue => [issue.field, issue.message]))
}
