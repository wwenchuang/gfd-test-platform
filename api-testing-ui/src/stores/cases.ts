import { defineStore } from 'pinia'

import { apiClient, ApiClientError, type ApiClient } from '../api/client'
import type { AiJob, ApiEndpoint, CaseDraft, CaseRequest, CaseValidation, CaseVersion, DebugResult, EnvironmentRevisionSnapshot, ExecutionCaseResult, ExecutionView, GeneratedCasePreview } from '../api/contracts'
import { aiValidationSummary } from '../utils/aiValidationPresentation'
import { validateCaseDraftLocally } from '../utils/caseDraftValidation'
import { hasLoadedCaseEvidence } from '../utils/executionPresentation'
import { createIdempotencyKey } from '../utils/idempotency'

const TERMINAL_AI = new Set(['completed', 'partial', 'failed', 'failed_gateway', 'failed_validation'])
const TERMINAL_EXECUTION = new Set(['DONE', 'CANCELLED', 'PASSED', 'FAILED', 'BROKEN'])

interface DraftScope {
  drafts: Record<string, CaseDraft>
  dirty: Record<string, true>
  identities: Record<string, number>
  activeVersions: Record<string, string>
  missingVersions: Record<string, string>
  previews: GeneratedCasePreview[]
  activePreview: string
}

// Keep the credential out of reactive store state and logs.
const storeSessions = new WeakMap<object, string | null>()
function sessionIdentity(): string | null {
  try { return globalThis.sessionStorage?.getItem('sessionToken') || null } catch { return null }
}

export const useCasesStore = defineStore('api-cases', {
  state: () => ({
    drafts: {} as Record<string, CaseDraft>,
    dirtyDrafts: {} as Record<string, true>,
    draftIdentities: {} as Record<string, number>,
    versions: {} as Record<string, CaseVersion>,
    loadGeneration: 0,
    versionMutationGeneration: 0,
    versionIdsByEndpoint: {} as Record<string, string[]>,
    activeVersionByEndpoint: {} as Record<string, string>,
    missingDraftVersionByEndpoint: {} as Record<string, string>,
    draftSourceRevisionId: '',
    draftScopeReady: false,
    draftScopeGeneration: 0,
    draftScopes: {} as Record<string, DraftScope>,
    generatedPreviews: [] as GeneratedCasePreview[],
    activeGeneratedPreviewId: '',
    aiJob: null as AiJob | null,
    aiError: '',
    aiPolling: false,
    aiCanResume: false,
    aiDiagnosisBatchId: '',
    lastAiJobId: '',
    aiRestoreGeneration: 0,
    aiScopeGeneration: 0,
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
    requireDraftSession(): void {
      if (storeSessions.has(this) && storeSessions.get(this) !== sessionIdentity()) {
        this.invalidateLoad()
        this.hideDraftScope()
        this.draftScopes = {}
        storeSessions.delete(this)
        throw new Error('登录状态已变化，请重新读取用例')
      }
    },
    startManualDraft(endpoint: ApiEndpoint): CaseDraft {
      if (this.draftSourceRevisionId && !this.draftScopeReady) throw new Error('当前来源尚未读取成功，请重新读取后再创建用例')
      this.draftIdentities[endpoint.id] = (this.draftIdentities[endpoint.id] || 0) + 1
      this.aiRestoreGeneration += 1
      delete this.activeVersionByEndpoint[endpoint.id]
      delete this.missingDraftVersionByEndpoint[endpoint.id]
      this.activeGeneratedPreviewId = ''
      this.drafts[endpoint.id] = blankDraft(endpoint)
      this.dirtyDrafts[endpoint.id] = true
      this.validationErrors = {}
      this.validationWarnings = {}
      this.savedMessage = ''
      this.clearDebug()
      return this.drafts[endpoint.id]
    },
    draftFor(endpoint: ApiEndpoint): CaseDraft {
      if (this.draftSourceRevisionId && !this.draftScopeReady) return blankDraft(endpoint)
      const versionId = this.activeVersionByEndpoint[endpoint.id]
      const version = versionId ? this.versions[versionId] : null
      if (!this.drafts[endpoint.id]) {
        this.drafts[endpoint.id] = version ? fromVersion(version) : blankDraft(endpoint)
      }
      if (version?.source_state === 'needs_adaptation') {
        this.drafts[endpoint.id].request.method = endpoint.method
        this.drafts[endpoint.id].request.path = endpoint.path
      }
      return this.drafts[endpoint.id]
    },
    updateDraft(endpointId: string, draft: CaseDraft): void {
      if (this.draftSourceRevisionId && !this.draftScopeReady) return
      this.aiRestoreGeneration += 1
      this.drafts[endpointId] = structuredClone(draft)
      this.dirtyDrafts[endpointId] = true
      this.savedMessage = ''
      this.validationErrors = {}
      this.validationWarnings = {}
    },
    setActiveVersion(endpointId: string, versionId: string): void {
      if (!this.versionIdsByEndpoint[endpointId]?.includes(versionId)) return
      this.draftIdentities[endpointId] = (this.draftIdentities[endpointId] || 0) + 1
      this.aiRestoreGeneration += 1
      this.activeVersionByEndpoint[endpointId] = versionId
      delete this.missingDraftVersionByEndpoint[endpointId]
      this.activeGeneratedPreviewId = ''
      const version = this.versions[versionId]
      if (version) this.drafts[endpointId] = fromVersion(version)
      delete this.dirtyDrafts[endpointId]
      this.validationErrors = {}
      this.validationWarnings = {}
      this.savedMessage = ''
      this.clearBaselineFeedback()
    },
    registerVersion(version: CaseVersion, makeActive = true): void {
      this.versionMutationGeneration += 1
      const sameVersion = this.versions[version.id]
      const nextVersion = sameVersion
        ? { ...version, lifecycle: { ...(sameVersion.lifecycle || {}), ...(version.lifecycle || {}) } }
        : version
      const previousVersions = Object.values(this.versions).filter(
        item => item.case_id === nextVersion.case_id,
      )
      if (previousVersions.some(item => item.version > nextVersion.version)) return
      for (const previous of previousVersions) {
        const previousEndpointId = displayEndpointId(previous)
        this.versionIdsByEndpoint[previousEndpointId] = (
          this.versionIdsByEndpoint[previousEndpointId] || []
        ).filter(id => id !== previous.id)
        if (this.activeVersionByEndpoint[previousEndpointId] === previous.id) {
          delete this.activeVersionByEndpoint[previousEndpointId]
        }
        if (previous.id !== nextVersion.id) delete this.versions[previous.id]
      }
      this.versions[nextVersion.id] = nextVersion
      const endpointId = displayEndpointId(nextVersion)
      const ids = this.versionIdsByEndpoint[endpointId] || []
      this.versionIdsByEndpoint[endpointId] = [...new Set([...ids, nextVersion.id])]
      if (makeActive || !this.activeVersionByEndpoint[endpointId]) {
        this.activeVersionByEndpoint[endpointId] = nextVersion.id
        this.drafts[endpointId] = fromVersion(nextVersion)
        if (makeActive) delete this.dirtyDrafts[endpointId]
      }
    },
    registerExternalVersion(version: CaseVersion, makeActive = true): void {
      const endpointId = displayEndpointId(version)
      if (!this.dirtyDrafts[endpointId]) {
        this.registerVersion(version, makeActive)
        return
      }
      const draft = cloneJson(this.drafts[endpointId])
      const originalVersion = this.missingDraftVersionByEndpoint[endpointId] || this.activeVersionByEndpoint[endpointId]
      this.registerVersion(version, false)
      this.drafts[endpointId] = draft
      this.dirtyDrafts[endpointId] = true
      if (!originalVersion) delete this.activeVersionByEndpoint[endpointId]
      else if (this.versions[originalVersion]) {
        this.activeVersionByEndpoint[endpointId] = originalVersion
        delete this.missingDraftVersionByEndpoint[endpointId]
      } else {
        delete this.activeVersionByEndpoint[endpointId]
        this.missingDraftVersionByEndpoint[endpointId] = originalVersion
      }
    },
    hideDraftScope(nextSource = ''): void {
      this.draftScopeGeneration += 1
      const previousSource = this.draftSourceRevisionId
      if (previousSource && this.draftScopeReady) {
        this.draftScopes[previousSource] = cloneJson({
          drafts: this.drafts, dirty: this.dirtyDrafts, identities: this.draftIdentities, activeVersions: this.activeVersionByEndpoint,
          missingVersions: this.missingDraftVersionByEndpoint,
          previews: this.generatedPreviews, activePreview: this.activeGeneratedPreviewId,
        })
      }
      this.draftSourceRevisionId = nextSource
      this.draftScopeReady = false
      this.drafts = {}
      this.dirtyDrafts = {}
      this.draftIdentities = {}
      this.versions = {}
      this.versionIdsByEndpoint = {}
      this.activeVersionByEndpoint = {}
      this.missingDraftVersionByEndpoint = {}
      this.generatedPreviews = []
      this.activeGeneratedPreviewId = ''
      this.basicGenerating = false
      this.clearAiJob()
      this.clearDebug()
    },
    invalidateLoad(): void { this.loadGeneration += 1 },
    async loadSavedCases(sourceRevisionId: string, client: Pick<ApiClient, 'get'> = apiClient, options: { preserveDrafts?: boolean } = {}): Promise<void | false> {
      const session = sessionIdentity()
      if (typeof window !== 'undefined' && !session) {
        this.hideDraftScope()
        this.draftScopes = {}
        storeSessions.delete(this)
        throw new Error('登录状态已失效，请重新登录后读取用例')
      }
      if (storeSessions.has(this) && storeSessions.get(this) !== session) {
        this.draftScopes = {}
        this.draftSourceRevisionId = ''
        this.draftScopeReady = false
        this.drafts = {}
        this.dirtyDrafts = {}
        this.draftIdentities = {}
        this.versions = {}
        this.versionIdsByEndpoint = {}
        this.activeVersionByEndpoint = {}
        this.missingDraftVersionByEndpoint = {}
        this.generatedPreviews = []
        this.activeGeneratedPreviewId = ''
        this.clearAiJob()
        this.clearDebug()
      }
      storeSessions.set(this, session)
      const previousSource = this.draftSourceRevisionId
      const changing = previousSource !== sourceRevisionId
      if (changing) {
        this.hideDraftScope(sourceRevisionId)
      }
      const generation = ++this.loadGeneration
      const mutationGeneration = this.versionMutationGeneration
      let response
      try {
        response = await client.get<{ case_versions: CaseVersion[] }>(
          `/api/api-testing/v1/cases?source_revision_id=${encodeURIComponent(sourceRevisionId)}`,
        )
      } catch (error) {
        if (generation !== this.loadGeneration) return false
        if (session !== sessionIdentity()) {
          this.hideDraftScope()
          this.draftScopes = {}
          storeSessions.delete(this)
          return false
        }
        this.hideDraftScope(sourceRevisionId)
        throw error
      }
      if (generation !== this.loadGeneration) return false
      if (session !== sessionIdentity()) {
        this.hideDraftScope()
        this.draftScopes = {}
        storeSessions.delete(this)
        return false
      }
      if (mutationGeneration !== this.versionMutationGeneration) return false
      const snapshot = this.draftScopeReady ? {
        drafts: this.drafts, dirty: this.dirtyDrafts, identities: this.draftIdentities, activeVersions: this.activeVersionByEndpoint,
        missingVersions: this.missingDraftVersionByEndpoint,
        previews: this.generatedPreviews, activePreview: this.activeGeneratedPreviewId,
      } : this.draftScopes[sourceRevisionId]
      const drafts = snapshot ? { ...snapshot.drafts } : null
      const previews = snapshot ? snapshot.previews : []
      const activePreview = snapshot ? snapshot.activePreview : ''
      const activeVersions = snapshot ? { ...snapshot.activeVersions } : {}
      const missingVersions = snapshot ? { ...snapshot.missingVersions } : {}
      const dirty = snapshot ? { ...snapshot.dirty } : {}
      const identities = snapshot ? { ...snapshot.identities } : {}
      this.drafts = {}
      this.dirtyDrafts = {}
      this.draftIdentities = identities
      this.versions = {}
      this.versionIdsByEndpoint = {}
      this.activeVersionByEndpoint = {}
      this.missingDraftVersionByEndpoint = {}
      this.generatedPreviews = previews
      this.activeGeneratedPreviewId = activePreview
      for (const version of response.data.case_versions) this.registerVersion(version, false)
      if (drafts) {
        for (const endpointId of Object.keys(drafts)) {
          const versionId = missingVersions[endpointId] || activeVersions[endpointId]
          if (!versionId && dirty[endpointId]) delete this.activeVersionByEndpoint[endpointId]
          else if (this.versions[versionId]) {
            this.activeVersionByEndpoint[endpointId] = versionId
            this.drafts[endpointId] = fromVersion(this.versions[versionId])
          }
          else if (versionId && dirty[endpointId]) {
            delete this.activeVersionByEndpoint[endpointId]
            this.missingDraftVersionByEndpoint[endpointId] = versionId
          }
          if (dirty[endpointId]) {
            this.drafts[endpointId] = drafts[endpointId]
            this.dirtyDrafts[endpointId] = true
          }
        }
      }
      this.draftScopeReady = true
    },
    async save(endpointId: string, environmentRevisionId?: string, options: { requireValidation?: boolean } = {}): Promise<CaseVersion> {
      this.requireDraftSession()
      if (this.saving) throw new Error('已有用例保存正在进行，请等待结果后再操作')
      if (this.draftSourceRevisionId && !this.draftScopeReady) throw new Error('当前来源尚未读取成功，请重新读取后再保存')
      const draft = this.drafts[endpointId]
      if (!draft) throw new Error('请先编辑测试用例')
      if (this.missingDraftVersionByEndpoint[endpointId]) throw new Error('原用例版本已不在当前列表中；草稿已保留，请重新选择用例或新建草稿后再保存')
      const sourceRevisionId = this.draftSourceRevisionId
      const scopeGeneration = this.draftScopeGeneration
      const session = sessionIdentity()
      const sentDraft = cloneJson(draft)
      const draftIdentity = this.draftIdentities[endpointId] || 0
      this.saving = true
      this.savedMessage = ''
      try {
        const existingId = this.activeVersionByEndpoint[endpointId]
        const existing = existingId ? this.versions[existingId] : null
        const path = existing
          ? `/api/api-testing/v1/cases/${existing.case_id}/versions`
          : '/api/api-testing/v1/cases'
        const body = existing
          ? { endpoint_id: endpointId, case: draft }
          : { endpoint_id: endpointId, case: draft, origin: 'manual' }
        const response = await apiClient.post<{ case_version: CaseVersion }>(path, body)
        const version = response.data.case_version
        if (session !== sessionIdentity()) throw new Error('用例已保存；登录状态已变化，请重新登录并核对结果')
        if (sourceRevisionId !== this.draftSourceRevisionId || scopeGeneration !== this.draftScopeGeneration) {
          const previous = this.draftScopes[sourceRevisionId]
          if (previous && previous.activeVersions[endpointId] === existingId && previous.drafts[endpointId]
              && (previous.identities[endpointId] || 0) === draftIdentity) {
            previous.activeVersions[endpointId] = version.id
            delete previous.missingVersions[endpointId]
            if (JSON.stringify(previous.drafts[endpointId]) === JSON.stringify(sentDraft)) {
              previous.drafts[endpointId] = fromVersion(version)
              delete previous.dirty[endpointId]
            }
          }
          if (sourceRevisionId === this.draftSourceRevisionId && this.draftScopeReady
              && this.activeVersionByEndpoint[endpointId] === existingId && this.drafts[endpointId]
              && (this.draftIdentities[endpointId] || 0) === draftIdentity) {
            delete this.activeVersionByEndpoint[endpointId]
            this.missingDraftVersionByEndpoint[endpointId] = version.id
          }
          throw new Error('用例已保存；范围已切换，请返回原来源核对结果')
        }
        const currentDraft = this.drafts[endpointId]
        const sameSelection = this.activeVersionByEndpoint[endpointId] === existingId
          && (this.draftIdentities[endpointId] || 0) === draftIdentity
        if (!sameSelection) throw new Error('用例已保存；当前选择已变化，请重新读取并核对结果')
        const newerDraft = currentDraft && JSON.stringify(currentDraft) !== JSON.stringify(sentDraft)
          ? cloneJson(currentDraft) : null
        this.registerVersion(version)
        if (newerDraft) {
          this.drafts[endpointId] = newerDraft
          this.dirtyDrafts[endpointId] = true
        }
        this.savedMessage = `草稿 v${version.version} 已保存`
        try {
          await this.validate(version.id, environmentRevisionId)
          if (scopeGeneration !== this.draftScopeGeneration || session !== sessionIdentity()) {
            throw new Error('用例已保存；范围或登录状态已变化，请返回原来源核对')
          }
        } catch (error) {
          if (scopeGeneration !== this.draftScopeGeneration || session !== sessionIdentity()) {
            throw new Error('用例已保存；范围或登录状态已变化，请返回原来源核对')
          }
          const reason = error instanceof Error ? error.message : '校验服务暂时不可用'
          this.validationErrors = {}
          this.validationWarnings = {
            post_save_validation: `草稿已保存，但保存后校验未完成：${reason}`,
          }
          this.savedMessage = `草稿 v${version.version} 已保存；校验暂未完成，可稍后重新保存或调试时重试`
          if (options.requireValidation) {
            throw new Error(`草稿已保存，但保存后校验未完成：${reason}`)
          }
        }
        return version
      } finally {
        this.saving = false
      }
    },
    async saveForDebug(endpointId: string, environmentRevisionId: string): Promise<CaseVersion> {
      const draft = this.drafts[endpointId]
      if (!draft) throw new Error('请先编辑测试用例')
      const draftIdentity = this.draftIdentities[endpointId] || 0
      const previewId = this.activeGeneratedPreviewId
      const localErrors = validateCaseDraftLocally(draft)
      if (Object.keys(localErrors).length) {
        this.validationErrors = localErrors
        this.validationWarnings = {}
        throw new Error(Object.values(localErrors)[0])
      }
      const version = await this.save(endpointId, environmentRevisionId, { requireValidation: true })
      if ((this.draftIdentities[endpointId] || 0) !== draftIdentity
          || this.activeGeneratedPreviewId !== previewId
          || this.activeVersionByEndpoint[endpointId] !== version.id
          || this.missingDraftVersionByEndpoint[endpointId]) {
        throw new Error('草稿已保存，但当前选择已变化；调试未开始，请重新确认')
      }
      if (this.dirtyDrafts[endpointId]) {
        throw new Error('草稿已保存，但保存期间又有修改；调试未开始，请确认最新内容后重试')
      }
      const firstError = Object.values(this.validationErrors)[0]
      if (firstError) throw new Error(`请先修正用例校验错误：${firstError}`)
      return version
    },
    async generate(endpointIds: string[], environmentRevisionId: string, intent: string, taskId?: string, thinkingMode?: 'off'): Promise<void> {
      this.clearAiJob()
      const scopeGeneration = this.aiScopeGeneration
      const session = sessionIdentity()
      const draftGeneration = this.aiRestoreGeneration
      try {
        const path = thinkingMode === 'off' ? '/api/api-testing/v1/ai-jobs/thinking-v1' : '/api/api-testing/v1/ai-jobs'
        const response = await apiClient.post<{ job: AiJob }>(path, {
          endpoint_ids: endpointIds, environment_revision_id: environmentRevisionId, intent,
          ...(taskId ? { task_id: taskId } : {}),
          ...(thinkingMode === 'off' ? { thinking_mode: 'off' } : {}),
        })
        if (scopeGeneration !== this.aiScopeGeneration || session !== sessionIdentity()) return
        this.aiJob = response.data.job
        this.lastAiJobId = response.data.job.id
        await this.pollAiJob(response.data.job.id, { draftGeneration })
      } catch (error) {
        if (scopeGeneration !== this.aiScopeGeneration || session !== sessionIdentity()) return
        this.aiError = thinkingMode === 'off' && error instanceof ApiClientError && error.status === 404
          ? '当前后台无法接受该请求，请检查版本及当前任务、接口的访问权限；未自动改用默认模式。'
          : error instanceof Error ? error.message : 'AI 生成失败'
      }
    },
    async generateBasicPositive(endpointIds: string[], environmentRevisionId: string, taskId?: string): Promise<CaseVersion[]> {
      this.requireDraftSession()
      const scopeGeneration = this.draftScopeGeneration
      const session = sessionIdentity()
      this.basicGenerating = true
      this.savedMessage = ''
      try {
        const response = await apiClient.post<{ case_versions: CaseVersion[] }>('/api/api-testing/v1/cases/basic-positive', {
          endpoint_ids: endpointIds,
          environment_revision_id: environmentRevisionId,
          ...(taskId ? { task_id: taskId } : {}),
        })
        if (scopeGeneration !== this.draftScopeGeneration || session !== sessionIdentity()) throw new Error('用例已生成；范围已切换，请返回原来源核对')
        const versions = response.data.case_versions
        for (const version of versions) this.registerExternalVersion(version)
        this.savedMessage = `已生成 ${versions.length} 个基础正向用例`
        return versions
      } finally {
        if (scopeGeneration === this.draftScopeGeneration && session === sessionIdentity()) this.basicGenerating = false
      }
    },
    async previewBasicPositive(endpointIds: string[], environmentRevisionId: string, taskId?: string): Promise<GeneratedCasePreview[]> {
      this.requireDraftSession()
      const scopeGeneration = this.draftScopeGeneration
      const session = sessionIdentity()
      this.basicGenerating = true
      this.savedMessage = ''
      try {
        const response = await apiClient.post<{ case_previews: GeneratedCasePreview[] }>('/api/api-testing/v1/cases/basic-positive/preview', {
          endpoint_ids: endpointIds,
          environment_revision_id: environmentRevisionId,
          ...(taskId ? { task_id: taskId } : {}),
        })
        if (scopeGeneration !== this.draftScopeGeneration || session !== sessionIdentity()) throw new Error('范围已切换，请在当前来源重新生成候选')
        const previews = response.data.case_previews
        const incomingIds = new Set(previews.map(item => item.id))
        const incomingEndpointIds = new Set(previews.map(item => item.endpoint_id))
        const activePreview = this.generatedPreviews.find(item => item.id === this.activeGeneratedPreviewId)
        const protectedId = activePreview && this.dirtyDrafts[activePreview.endpoint_id] ? activePreview.id : ''
        this.generatedPreviews = [
          ...this.generatedPreviews.filter(item => item.id === protectedId || (!incomingIds.has(item.id) && !incomingEndpointIds.has(item.endpoint_id))),
          ...previews.filter(item => item.id !== protectedId).map(item => cloneJson(item)),
        ]
        this.savedMessage = `已生成 ${previews.length} 个基础正向候选，请确认后保存`
        return previews
      } finally {
        if (scopeGeneration === this.draftScopeGeneration && session === sessionIdentity()) this.basicGenerating = false
      }
    },
    setDraftFromGeneratedPreview(previewId: string): void {
      if (this.draftSourceRevisionId && !this.draftScopeReady) return
      const preview = this.generatedPreviews.find(item => item.id === previewId)
      if (!preview) return
      this.draftIdentities[preview.endpoint_id] = (this.draftIdentities[preview.endpoint_id] || 0) + 1
      this.drafts[preview.endpoint_id] = cloneJson(preview.case)
      this.dirtyDrafts[preview.endpoint_id] = true
      delete this.activeVersionByEndpoint[preview.endpoint_id]
      delete this.missingDraftVersionByEndpoint[preview.endpoint_id]
      this.activeGeneratedPreviewId = preview.id
      this.validationErrors = {}
      this.validationWarnings = {}
      this.savedMessage = ''
      this.clearDebug()
      this.clearBaselineFeedback()
    },
    async saveGeneratedPreview(previewId: string, draft?: CaseDraft): Promise<CaseVersion> {
      this.requireDraftSession()
      if (this.saving) throw new Error('已有用例保存正在进行，请等待结果后再操作')
      const preview = this.generatedPreviews.find(item => item.id === previewId)
      if (!preview) throw new Error('请选择要保存的基础正向候选')
      const sourceRevisionId = this.draftSourceRevisionId
      const scopeGeneration = this.draftScopeGeneration
      const session = sessionIdentity()
      const originalActivePreview = this.activeGeneratedPreviewId
      const originalActiveVersion = this.activeVersionByEndpoint[preview.endpoint_id]
      const originalDraft = this.drafts[preview.endpoint_id] ? cloneJson(this.drafts[preview.endpoint_id]) : null
      const draftIdentity = this.draftIdentities[preview.endpoint_id] || 0
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
        if (session !== sessionIdentity()) throw new Error('用例已保存；登录状态已变化，请重新登录并核对结果')
        if (scopeGeneration !== this.draftScopeGeneration) {
          const previous = this.draftScopes[sourceRevisionId]
          if (previous) {
            previous.previews = previous.previews.filter(item => item.id !== previewId)
            if (previous.activePreview === previewId) {
              previous.activePreview = ''
              previous.activeVersions[preview.endpoint_id] = version.id
              delete previous.missingVersions[preview.endpoint_id]
              if (JSON.stringify(previous.drafts[preview.endpoint_id]) === JSON.stringify(caseDraft)) {
                previous.drafts[preview.endpoint_id] = fromVersion(version)
                delete previous.dirty[preview.endpoint_id]
              }
            }
          }
          if (sourceRevisionId === this.draftSourceRevisionId && this.draftScopeReady
              && this.activeGeneratedPreviewId === previewId) {
            this.generatedPreviews = this.generatedPreviews.filter(item => item.id !== previewId)
            this.activeGeneratedPreviewId = ''
            delete this.activeVersionByEndpoint[preview.endpoint_id]
            this.missingDraftVersionByEndpoint[preview.endpoint_id] = version.id
          }
          throw new Error('用例已保存；范围已切换，请返回原来源核对结果')
        }
        if (originalActivePreview !== this.activeGeneratedPreviewId
            || originalActiveVersion !== this.activeVersionByEndpoint[preview.endpoint_id]
            || draftIdentity !== (this.draftIdentities[preview.endpoint_id] || 0)) {
          this.generatedPreviews = this.generatedPreviews.filter(item => item.id !== previewId)
          throw new Error('用例已保存；当前选择已变化，请重新读取并核对结果')
        }
        if (originalActivePreview !== previewId && this.dirtyDrafts[preview.endpoint_id]) {
          this.registerExternalVersion(version, false)
          this.generatedPreviews = this.generatedPreviews.filter(item => item.id !== previewId)
          this.savedMessage = '基础正向用例已保存，当前未保存的编辑已保留'
          return version
        }
        const currentDraft = this.drafts[preview.endpoint_id]
        const newerDraft = currentDraft && (JSON.stringify(currentDraft) !== JSON.stringify(originalDraft)
          || (this.dirtyDrafts[preview.endpoint_id] && originalActivePreview !== previewId)) ? cloneJson(currentDraft) : null
        this.registerVersion(version)
        this.discardGeneratedPreview(preview.id)
        if (newerDraft) {
          this.drafts[preview.endpoint_id] = newerDraft
          this.dirtyDrafts[preview.endpoint_id] = true
        }
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
      this.requireDraftSession()
      if (this.saving) throw new Error('已有用例保存正在进行，请等待结果后再操作')
      const scopeGeneration = this.draftScopeGeneration
      const session = sessionIdentity()
      this.saving = true
      this.savedMessage = ''
      try {
        const response = await apiClient.put<{ case_version: CaseVersion }>(
          `/api/api-testing/v1/case-versions/${versionId}/group`,
          { group_name: groupName },
        )
        const version = response.data.case_version
        if (scopeGeneration !== this.draftScopeGeneration || session !== sessionIdentity()) throw new Error('分组已更新；范围已切换，请返回原来源核对')
        this.registerExternalVersion(version, false)
        this.savedMessage = `已移动到分组“${version.group_name}”`
        return version
      } finally {
        this.saving = false
      }
    },
    async updateVersionGroups(versionIds: string[], groupName: string): Promise<CaseVersion[]> {
      this.requireDraftSession()
      if (this.saving) throw new Error('已有用例保存正在进行，请等待结果后再操作')
      const scopeGeneration = this.draftScopeGeneration
      const session = sessionIdentity()
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
            if (scopeGeneration !== this.draftScopeGeneration || session !== sessionIdentity()) {
              throw new Error(`范围已切换；已有 ${updated.length + 1} 条分组更新提交，请返回原来源核对`)
            }
            updated.push(response.data.case_version)
            this.registerExternalVersion(response.data.case_version, false)
          } catch (error) {
            if (scopeGeneration !== this.draftScopeGeneration || session !== sessionIdentity()) {
              throw error instanceof Error && error.message.startsWith('范围已切换；')
                ? error : new Error(`范围已切换；已有 ${updated.length} 条完成，当前请求可能已提交，请返回原来源核对`)
            }
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
          delete this.dirtyDrafts[preview.endpoint_id]
        }
      }
    },
    async pollAiJob(jobId: string, options: { maxAttempts?: number; delayMs?: number; draftGeneration?: number } = {}): Promise<void> {
      const scopeGeneration = ++this.aiScopeGeneration
      const session = sessionIdentity()
      const restoreGeneration = options.draftGeneration ?? ++this.aiRestoreGeneration
      const isCurrent = () => scopeGeneration === this.aiScopeGeneration && session === sessionIdentity()
      this.aiDiagnosisBatchId = ''
      const maxAttempts = options.maxAttempts ?? 120
      const delayMs = options.delayMs ?? 1500
      this.aiPolling = true
      this.aiCanResume = false
      this.lastAiJobId = jobId
      try {
        for (let attempt = 0; attempt < maxAttempts; attempt += 1) {
          if (!isCurrent()) return
          const response = await apiClient.get<{ job: AiJob }>(`/api/api-testing/v1/ai-jobs/${jobId}`)
          if (!isCurrent()) return
          this.aiJob = response.data.job
          if (TERMINAL_AI.has(this.aiJob.state)) {
            this.aiError = aiJobFailureMessage(this.aiJob)
            for (const batch of this.aiJob.batches) {
              for (const versionId of batch.generated_draft_ids) {
                if (!isCurrent()) return
                await this.loadVersion(versionId, apiClient, restoreGeneration)
              }
            }
            return
          }
          if (delayMs > 0) await delay(delayMs)
        }
        if (!isCurrent()) return
        this.aiCanResume = true
        this.aiError = this.aiJob?.state === 'queued'
          ? '最近状态显示任务仍在排队，尚未确认执行器接收；可能持续排队，请继续查看或联系管理员检查执行器。'
          : this.aiJob?.state === 'running'
            ? '最近状态显示任务生成中，点击“继续查看”获取最新进度。'
            : '暂未确认任务最新状态，点击“继续查看”重新读取。'
      } catch {
        if (!isCurrent()) return
        this.aiCanResume = true
        this.aiError = '无法读取任务状态，任务可能仍在排队或生成；请点击“继续查看”重新读取。'
      } finally {
        if (isCurrent()) this.aiPolling = false
      }
    },
    async resumeAiJob(): Promise<void> {
      if (!this.lastAiJobId || this.aiPolling) return
      this.aiError = ''
      await this.pollAiJob(this.lastAiJobId)
    },
    async diagnoseAiValidation(batchId: string, errorIndex: number): Promise<void> {
      if (!this.aiJob || this.aiDiagnosisBatchId) return
      const scopeGeneration = this.aiScopeGeneration
      const session = sessionIdentity()
      this.aiDiagnosisBatchId = batchId
      this.aiError = ''
      try {
        const response = await apiClient.post<{ job: AiJob }>(
          `/api/api-testing/v1/ai-jobs/${this.aiJob.id}/validation-diagnosis`,
          { batch_id: batchId, error_index: errorIndex },
        )
        if (scopeGeneration !== this.aiScopeGeneration || session !== sessionIdentity()) return
        this.aiJob = response.data.job
      } catch (error) {
        if (scopeGeneration !== this.aiScopeGeneration || session !== sessionIdentity()) return
        this.aiError = error instanceof Error
          ? error.message
          : '千问暂时无法分析该错误，请稍后重试'
      } finally {
        if (scopeGeneration === this.aiScopeGeneration && session === sessionIdentity()) this.aiDiagnosisBatchId = ''
      }
    },
    clearAiJob(): void {
      this.aiRestoreGeneration += 1
      this.aiScopeGeneration += 1
      this.aiJob = null
      this.aiError = ''
      this.aiPolling = false
      this.aiCanResume = false
      this.aiDiagnosisBatchId = ''
      this.lastAiJobId = ''
    },
    async restoreLatestAiJob(
      projectId: string,
      sourceRevisionId?: string,
      client: Pick<ApiClient, 'get'> = apiClient,
    ): Promise<void> {
      this.clearAiJob()
      const restoreGeneration = this.aiRestoreGeneration
      const session = sessionIdentity()
      try {
        const sourceQuery = sourceRevisionId
          ? `&source_revision_id=${encodeURIComponent(sourceRevisionId)}`
          : ''
        const response = await client.get<{ job: AiJob | null }>(
          `/api/api-testing/v1/ai-jobs/latest?project_id=${encodeURIComponent(projectId)}${sourceQuery}`,
        )
        if (restoreGeneration !== this.aiRestoreGeneration || session !== sessionIdentity()) return
        const job = response.data.job
        if (!job) return
        this.aiJob = job
        this.lastAiJobId = job.id
        if (TERMINAL_AI.has(job.state)) {
          this.aiCanResume = false
          this.aiError = aiJobFailureMessage(job)
          for (const versionId of new Set(job.batches.flatMap(batch => batch.generated_draft_ids))) {
            if (restoreGeneration !== this.aiRestoreGeneration) return
            await this.loadVersion(versionId, client, restoreGeneration)
          }
          return
        }
        this.aiCanResume = true
        this.aiError = job.state === 'queued'
          ? '最近状态显示任务仍在排队，尚未确认执行器接收；可能持续排队，请继续查看或联系管理员检查执行器。'
          : '发现未完成的 AI 任务，点击“继续查看”获取最新状态。'
      } catch {
        // Workspace startup remains usable when no prior AI job can be restored.
      }
    },
    async loadVersion(
      versionId: string,
      client: Pick<ApiClient, 'get'> = apiClient,
      restoreGeneration?: number,
    ): Promise<void> {
      const sourceRevisionId = this.draftSourceRevisionId
      const scopeGeneration = this.draftScopeGeneration
      const session = sessionIdentity()
      const response = await client.get<{ case_version: CaseVersion }>(`/api/api-testing/v1/case-versions/${versionId}`)
      if (sourceRevisionId !== this.draftSourceRevisionId || scopeGeneration !== this.draftScopeGeneration || session !== sessionIdentity()) return
      if (restoreGeneration !== undefined && restoreGeneration !== this.aiRestoreGeneration) return
      const version = response.data.case_version
      this.registerExternalVersion(version)
    },
    async archiveCase(endpointId: string, versionId: string): Promise<void> {
      this.requireDraftSession()
      const scopeGeneration = this.draftScopeGeneration
      const session = sessionIdentity()
      const version = this.versions[versionId]
      if (!version) throw new Error('请选择要删除的用例')
      await apiClient.delete(`/api/api-testing/v1/cases/${version.case_id}`)
      if (scopeGeneration !== this.draftScopeGeneration || session !== sessionIdentity()) throw new Error('删除请求已完成；范围已切换，请返回原来源核对')
      this.versionMutationGeneration += 1
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
          delete this.dirtyDrafts[endpointId]
        }
      }
      this.clearDebug()
      this.validationErrors = {}
      this.validationWarnings = {}
      this.savedMessage = '用例已删除，历史执行和基线证据仍保留'
    },
    async validate(versionId: string, environmentRevisionId?: string): Promise<void> {
      const scopeGeneration = this.draftScopeGeneration
      const session = sessionIdentity()
      this.validationErrors = {}
      this.validationWarnings = {}
      let environmentMetadata: Record<string, unknown> = {}
      if (environmentRevisionId) {
        const environmentResponse = await apiClient.get<{ environment_revision: EnvironmentRevisionSnapshot }>(
          `/api/api-testing/v1/environment-revisions/${environmentRevisionId}`,
        )
        if (scopeGeneration !== this.draftScopeGeneration || session !== sessionIdentity()) throw new Error('范围已切换，校验未完成')
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
      if (scopeGeneration !== this.draftScopeGeneration || session !== sessionIdentity()) throw new Error('范围已切换，校验未完成')
      const validation = response.data.validation
      if (!validation) return
      this.validationErrors = issueMap(validation.errors || [])
      this.validationWarnings = issueMap(validation.warnings || [])
    },
    async debug(input: { projectId: string; sourceRevisionId: string; environmentRevisionId: string; caseVersionId: string; taskId?: string; isCurrent?: () => boolean }): Promise<void> {
      if (this.debugPolling) throw new Error('调试请求正在提交或执行，请稍候。')
      const generation = ++this.debugGeneration
      this.debugResult = null
      this.debugError = ''
      this.debugCanResume = false
      this.clearBaselineFeedback()
      this.debugPolling = true
      try {
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
        if (generation !== this.debugGeneration || input.isCurrent?.() === false) return
        this.debugExecution = response.data.execution
        await this.pollExecution(response.data.execution.id, { isCurrent: input.isCurrent })
      } finally {
        if (generation === this.debugGeneration) this.debugPolling = false
      }
    },
    async pollExecution(executionId: string, options: { maxAttempts?: number; delayMs?: number; isCurrent?: () => boolean } = {}): Promise<void> {
      const maxAttempts = options.maxAttempts ?? 240
      const delayMs = options.delayMs ?? 1000
      const generation = this.debugGeneration
      const isCurrent = () => generation === this.debugGeneration && options.isCurrent?.() !== false
      this.debugPolling = true
      this.debugCanResume = false
      this.debugError = ''
      try {
        for (let attempt = 0; attempt < maxAttempts; attempt += 1) {
          if (!isCurrent()) return
          const response = await apiClient.get<{ execution: ExecutionView }>(`/api/api-testing/v1/executions/${executionId}`)
          if (!isCurrent()) return
          this.debugExecution = response.data.execution
          if (TERMINAL_EXECUTION.has(this.debugExecution.state)) {
            let result = this.debugExecution.case_results.find(
              item => item.execution_role === 'requested',
            ) || this.debugExecution.case_results[0]
            if (result && !hasLoadedCaseEvidence(result)) {
              const evidence = await apiClient.get<{ case_result: ExecutionCaseResult }>(
                `/api/api-testing/v1/executions/${executionId}/cases/${result.execution_case_id}`,
              )
              if (!isCurrent()) return
              result = evidence.data.case_result
              this.debugExecution = {
                ...this.debugExecution,
                case_results: this.debugExecution.case_results.map(item => (
                  item.execution_case_id === result?.execution_case_id ? result : item
                )),
              }
            }
            if (result) this.debugResult = toDebugResult(result)
            this.debugCanResume = false
            return
          }
          if (delayMs > 0) await delay(delayMs)
        }
        if (!isCurrent()) return
        this.debugCanResume = true
        this.debugError = '调试仍在后台执行，可关闭抽屉后从执行记录继续查看'
      } catch (error) {
        if (!isCurrent()) return
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
    request: endpointRequestFromDefinition(endpoint),
    data_rows: [], assertions: [{ type: 'status_code', operator: 'equals', expected: 200, timeout_ms: 0, enabled: true }],
    extractions: [], dependencies: [], processing: { pre: [], post: [], setup_steps: [], cleanup_steps: [] },
  }
}

export function endpointRequestFromDefinition(endpoint: ApiEndpoint): CaseRequest {
  return {
    method: endpoint.method,
    path: endpoint.path,
    service: 'default',
    ...requestParameterExamples(endpoint.operation),
    headers: {},
    body: requestBodyExample(endpoint.operation),
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
      ...(version.processing?.response_time_warning_ms == null ? {} : { response_time_warning_ms: version.processing.response_time_warning_ms }),
    },
  })
}

function displayEndpointId(version: CaseVersion): string {
  return version.current_endpoint_id || version.endpoint_id
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

function aiJobFailureMessage(job: AiJob): string {
  if (!['failed', 'failed_gateway', 'failed_validation'].includes(job.state)) return ''
  if (job.state === 'failed_gateway') {
    const failedBatches = job.batches.filter(batch => batch.state === 'failed_gateway')
    const validationFailed = job.batches.some(batch => batch.state === 'failed_validation' || batch.validation_errors?.length)
    if (failedBatches.some(batch => batch.error_code === 'generation_uncertain')) {
      const validationNote = validationFailed ? '，并有生成结果未通过校验' : ''
      return `部分批次生成结果不确定${validationNote}，模型调用可能产生费用；请先核对任务和草稿，再决定是否重试`
    }
    if (failedBatches.some(batch => batch.error_code === 'compatible_worker_timeout')) {
      const validationNote = validationFailed ? '，并有生成结果未通过校验' : ''
      return `部分批次等待兼容执行器超时${validationNote}，这些批次未发起模型调用；请联系管理员检查执行器和队列`
    }
    if (failedBatches.length && validationFailed) {
      const allTimeout = failedBatches.every(batch => batch.error_code === 'gateway_timeout')
      const allEnqueue = failedBatches.every(batch => batch.error_code === 'enqueue_failed')
      const gatewayMessage = allTimeout ? '部分批次模型服务或连接等待超时'
        : allEnqueue ? '部分批次未能进入执行队列' : '部分批次模型调用或提交失败'
      return `${gatewayMessage}，并有生成结果未通过校验；请检查模型服务及测试意图后重试`
    }
    const allBatchesFailed = failedBatches.length > 0 && failedBatches.length === job.batches.length
    if (allBatchesFailed && failedBatches.every(batch => batch.error_code === 'enqueue_failed')) {
      return '任务未能进入执行队列，未发起模型调用；请稍后重试或联系管理员检查任务队列'
    }
    if (allBatchesFailed && failedBatches.every(batch => batch.error_code === 'gateway_timeout')) {
      return '模型服务或连接等待超时，本次未生成新草稿；请稍后重试或联系管理员检查模型服务'
    }
  }
  const issues = job.batches.flatMap(batch => batch.validation_errors || [])
  const issue = issues.find(item => typeof item.message === 'string')
  if (issue?.code === 'missing_endpoint_coverage') {
    return '部分已选接口没有生成有效用例，请调整测试意图后重试'
  }
  if (issue?.message === 'AI Gateway content is not strict JSON') {
    return 'AI 返回内容格式不正确，请重新生成'
  }
  if (typeof issue?.message === 'string' && /[\u4e00-\u9fff]/.test(issue.message)) return issue.message
  const translated = aiValidationSummary(issues)
  if (translated) return translated
  if (job.state === 'failed_gateway') return 'AI 服务调用失败，请稍后重试或检查模型配置'
  if (job.state === 'failed_validation') return 'AI 生成结果未通过平台校验，请调整测试意图后重试'
  return 'AI 生成未完成，请重新生成当前范围'
}

function issueMap(issues: Array<{ field: string; message: string }>): Record<string, string> {
  return Object.fromEntries(issues.map(issue => [issue.field, issue.message]))
}
