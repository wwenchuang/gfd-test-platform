import { defineStore } from 'pinia'
import { markRaw } from 'vue'

import { apiClient } from '../api/client'
import type { ExecutionCaseResult, ExecutionConnectionState, ExecutionEventView, ExecutionView } from '../api/contracts'
import { createIdempotencyKey } from '../utils/idempotency'
import { hasLoadedCaseEvidence } from '../utils/executionPresentation'

const TERMINAL = new Set(['DONE', 'CANCELLED', 'PASSED', 'FAILED', 'BROKEN'])
const EVENT_TYPES = [
  'execution_queued', 'execution_started', 'case_started', 'request', 'response',
  'assertion', 'extraction', 'failure_analysis', 'failure_analysis_unavailable', 'case_finished', 'cancellation_requested', 'failure', 'execution_finished',
  'notification_sent', 'notification_failed',
]
const RECONNECT_DELAYS_MS = [1000, 2000, 5000, 10000, 30000]
const ANALYSIS_REFRESH_DELAYS_MS = [500, 1000, 2000, 4000, 8000, 10000]
const FINAL_SNAPSHOT_POLL_MS = 5000

export const useExecutionsStore = defineStore('api-executions', {
  state: () => ({
    executions: [] as ExecutionView[],
    archivedExecutionIds: new Set<string>(),
    active: null as ExecutionView | null,
    events: [] as ExecutionEventView[],
    connectionState: 'idle' as ExecutionConnectionState,
    loading: false,
    selectingExecutionId: '',
    baselineStarting: false,
    deleting: false,
    error: '',
    loadingCaseKeys: [] as string[],
    caseEvidenceErrors: {} as Record<string, string>,
    eventSource: null as EventSource | null,
    reconnectTimer: null as ReturnType<typeof setTimeout> | null,
    reconnectAttempts: 0,
    selectionVersion: 0,
    analysisRefreshTimer: null as ReturnType<typeof setTimeout> | null,
    analysisRefreshAttempts: 0,
    finalSnapshotTimer: null as ReturnType<typeof setTimeout> | null,
  }),
  actions: {
    prepareSelection(executionId: string): void {
      if (this.active?.id !== executionId) {
        this.selectionVersion += 1
        this.disconnect()
        this.active = null
        this.events = []
      }
      this.error = ''
      this.selectingExecutionId = executionId
    },
    async load(projectId: string): Promise<void> {
      this.loading = true
      this.error = ''
      try {
        const response = await apiClient.get<{ executions: ExecutionView[] }>(
          `/api/api-testing/v1/executions?project_id=${encodeURIComponent(projectId)}&limit=50`,
        )
        const previousById = new Map(this.executions.map(item => [item.id, item]))
        if (this.active) previousById.set(this.active.id, this.active)
        this.executions = response.data.executions
          .filter(item => !this.archivedExecutionIds.has(item.id))
          .map(item => mergeLoadedCaseEvidence(item, previousById.get(item.id)))
        if (this.active) this.active = this.executions.find(item => item.id === this.active?.id) || this.active
      } catch (error) {
        this.error = error instanceof Error ? error.message : '无法读取执行记录'
      } finally {
        this.loading = false
      }
    },
    async loadExecution(executionId: string): Promise<ExecutionView> {
      const response = await apiClient.get<{ execution: ExecutionView }>(
        `/api/api-testing/v1/executions/${executionId}`,
      )
      const previous = this.active?.id === executionId
        ? this.active
        : this.executions.find(item => item.id === executionId)
      const execution = mergeLoadedCaseEvidence(response.data.execution, previous)
      if (this.archivedExecutionIds.has(executionId)) return execution
      if (this.active && this.active.id !== executionId) return execution
      this.active = execution
      const index = this.executions.findIndex(item => item.id === executionId)
      if (index >= 0) this.executions[index] = this.active
      else this.executions.unshift(this.active)
      return this.active
    },
    async loadExecutionCase(
      executionId: string,
      executionCaseId: string,
      force = false,
    ): Promise<ExecutionCaseResult> {
      const key = `${executionId}:${executionCaseId}`
      const currentExecution = this.active?.id === executionId
        ? this.active
        : this.executions.find(item => item.id === executionId)
      const current = currentExecution?.case_results.find(item => item.execution_case_id === executionCaseId)
      if (!force && current && hasLoadedCaseEvidence(current)) return current
      this.loadingCaseKeys = [...new Set([...this.loadingCaseKeys, key])]
      const errors = { ...this.caseEvidenceErrors }
      delete errors[key]
      this.caseEvidenceErrors = errors
      try {
        const response = await apiClient.get<{ case_result: ExecutionCaseResult }>(
          `/api/api-testing/v1/executions/${executionId}/cases/${executionCaseId}`,
        )
        const result = response.data.case_result
        if (!this.archivedExecutionIds.has(executionId)) {
          if (this.active?.id === executionId) this.active = replaceExecutionCase(this.active, result)
          const index = this.executions.findIndex(item => item.id === executionId)
          if (index >= 0) this.executions[index] = replaceExecutionCase(this.executions[index], result)
        }
        return result
      } catch (error) {
        this.caseEvidenceErrors = {
          ...this.caseEvidenceErrors,
          [key]: error instanceof Error ? error.message : '无法读取当前用例证据',
        }
        throw error
      } finally {
        this.loadingCaseKeys = this.loadingCaseKeys.filter(item => item !== key)
      }
    },
    async select(executionId: string): Promise<void> {
      this.prepareSelection(executionId)
      const selectionVersion = this.selectionVersion
      this.reconnectAttempts = 0
      try {
        const response = await apiClient.get<{ execution: ExecutionView }>(
          `/api/api-testing/v1/executions/${executionId}`,
        )
        if (selectionVersion !== this.selectionVersion || this.archivedExecutionIds.has(executionId)) return
        const previous = this.executions.find(item => item.id === executionId)
        const execution = mergeLoadedCaseEvidence(response.data.execution, previous)
        this.active = execution
        const index = this.executions.findIndex(item => item.id === executionId)
        if (index >= 0) this.executions[index] = execution
        else this.executions.unshift(execution)
        await this.connect(executionId)
      } finally {
        if (selectionVersion === this.selectionVersion && this.selectingExecutionId === executionId) {
          this.selectingExecutionId = ''
        }
      }
    },
    appendEvent(event: ExecutionEventView): void {
      if (!Number.isInteger(event.id) || event.id <= 0) return
      if (this.events.some(item => item.id === event.id)) return
      if (this.events.length && event.id < this.events[this.events.length - 1].id) return
      this.events.push(event)
    },
    async connect(executionId: string): Promise<void> {
      const selectionVersion = this.selectionVersion
      if (this.eventSource) this.disconnect(false)
      this.connectionState = 'connecting'
      this.error = ''
      try {
        const response = await apiClient.post<{ ticket: string }>(
          `/api/api-testing/v1/executions/${executionId}/sse-ticket`, {},
        )
        if (selectionVersion !== this.selectionVersion) return
        if (this.active && this.active.id !== executionId) return
        const ticket = response.data.ticket
        const afterId = this.events.at(-1)?.id || 0
        const after = afterId > 0 ? `&after=${afterId}` : ''
        const source = markRaw(new EventSource(
          `/api/api-testing/v1/executions/${encodeURIComponent(executionId)}/events?ticket=${encodeURIComponent(ticket)}${after}`,
        ))
        this.eventSource = source
        source.onopen = () => {
          this.reconnectAttempts = 0
          this.connectionState = 'open'
        }
        source.onerror = () => {
          if (this.eventSource !== source) return
          source.close()
          this.eventSource = null
          if (this.active && TERMINAL.has(this.active.state)) {
            this.connectionState = 'complete'
          } else {
            this.scheduleReconnect(executionId)
          }
        }
        for (const type of EVENT_TYPES) {
          source.addEventListener(type, event => {
            if (this.eventSource !== source) return
            this.consumeEvent(type, event as MessageEvent, executionId)
          })
        }
      } catch (error) {
        this.error = error instanceof Error ? error.message : '实时日志连接失败'
        this.scheduleReconnect(executionId)
      }
    },
    scheduleReconnect(executionId: string): void {
      if (this.active && this.active.id !== executionId) return
      if (this.active && TERMINAL.has(this.active.state)) {
        this.connectionState = 'complete'
        return
      }
      if (this.reconnectAttempts >= RECONNECT_DELAYS_MS.length) {
        this.connectionState = 'failed'
        this.error = '实时日志重新连接失败，请手动重新连接'
        this.scheduleFinalSnapshotPoll(executionId)
        return
      }
      const delay = RECONNECT_DELAYS_MS[this.reconnectAttempts] ?? RECONNECT_DELAYS_MS.at(-1)!
      const selectionVersion = this.selectionVersion
      this.reconnectAttempts += 1
      this.connectionState = 'reconnecting'
      if (this.reconnectTimer) clearTimeout(this.reconnectTimer)
      this.reconnectTimer = setTimeout(() => {
        this.reconnectTimer = null
        if (selectionVersion !== this.selectionVersion) return
        if (this.active && this.active.id !== executionId) return
        void this.connect(executionId)
      }, delay)
    },
    async reconnect(executionId: string): Promise<void> {
      if (this.finalSnapshotTimer) clearTimeout(this.finalSnapshotTimer)
      this.finalSnapshotTimer = null
      this.reconnectAttempts = 0
      this.error = ''
      await this.connect(executionId)
    },
    scheduleFinalSnapshotPoll(executionId: string): void {
      if (this.finalSnapshotTimer || (this.active && this.active.id !== executionId)) return
      const selectionVersion = this.selectionVersion
      this.finalSnapshotTimer = setTimeout(async () => {
        this.finalSnapshotTimer = null
        if (selectionVersion !== this.selectionVersion || (this.active && this.active.id !== executionId)) return
        try {
          const execution = await this.loadExecution(executionId)
          if (selectionVersion !== this.selectionVersion || this.active?.id !== executionId) return
          if (TERMINAL.has(execution.state)) {
            this.connectionState = 'complete'
            this.analysisRefreshAttempts = 0
            const pendingAnalysis = execution.case_results.some(
              item => ['FAILED', 'BROKEN'].includes(item.status) && !item.failure_analysis,
            )
            if (pendingAnalysis) void this.refreshPendingAnalysis(executionId)
            return
          }
        } catch (error) {
          if (selectionVersion !== this.selectionVersion || this.active?.id !== executionId) return
          this.error = error instanceof Error ? error.message : '无法刷新执行终态'
        }
        this.scheduleFinalSnapshotPoll(executionId)
      }, FINAL_SNAPSHOT_POLL_MS)
    },
    consumeEvent(type: string, event: MessageEvent, executionId: string): void {
      let payload: Record<string, unknown> = {}
      try { payload = JSON.parse(String(event.data || '{}')) as Record<string, unknown> } catch { payload = { message: '事件内容无法解析' } }
      const id = Number(event.lastEventId)
      this.appendEvent(toEvent(id, type, payload))
      if (type === 'execution_finished') {
        this.connectionState = 'complete'
        this.disconnect(false)
        this.analysisRefreshAttempts = 0
        void this.refreshPendingAnalysis(executionId)
      }
    },
    async refreshPendingAnalysis(executionId: string): Promise<void> {
      const selectionVersion = this.selectionVersion
      const execution = await this.loadExecution(executionId)
      if (selectionVersion !== this.selectionVersion || this.active?.id !== executionId) return
      const pending = execution.case_results.some(
        item => ['FAILED', 'BROKEN'].includes(item.status) && !item.failure_analysis,
      )
      if (!pending || this.analysisRefreshAttempts >= ANALYSIS_REFRESH_DELAYS_MS.length) {
        this.analysisRefreshAttempts = 0
        return
      }
      const delay = ANALYSIS_REFRESH_DELAYS_MS[this.analysisRefreshAttempts] ?? 10000
      this.analysisRefreshAttempts += 1
      if (this.analysisRefreshTimer) clearTimeout(this.analysisRefreshTimer)
      this.analysisRefreshTimer = setTimeout(() => {
        this.analysisRefreshTimer = null
        if (selectionVersion !== this.selectionVersion || this.active?.id !== executionId) return
        void this.refreshPendingAnalysis(executionId)
      }, delay)
    },
    disconnect(resetState = true): void {
      this.eventSource?.close()
      this.eventSource = null
      if (this.reconnectTimer) clearTimeout(this.reconnectTimer)
      this.reconnectTimer = null
      if (this.analysisRefreshTimer) clearTimeout(this.analysisRefreshTimer)
      this.analysisRefreshTimer = null
      if (this.finalSnapshotTimer) clearTimeout(this.finalSnapshotTimer)
      this.finalSnapshotTimer = null
      if (resetState) {
        this.reconnectAttempts = 0
        this.analysisRefreshAttempts = 0
        this.connectionState = 'idle'
      }
    },
    async cancel(executionId: string): Promise<void> {
      const response = await apiClient.post<{ execution: ExecutionView }>(
        `/api/api-testing/v1/executions/${executionId}/cancel`, {},
      )
      this.active = response.data.execution
    },
    async deleteExecution(executionId: string): Promise<void> {
      await this.deleteExecutions([executionId])
    },
    async deleteExecutions(executionIds: string[]): Promise<void> {
      const ids = [...new Set(executionIds)].filter(Boolean)
      if (!ids.length) return
      this.deleting = true
      this.error = ''
      try {
        if (ids.length === 1) {
          await apiClient.delete<{ execution: ExecutionView }>(
            `/api/api-testing/v1/executions/${encodeURIComponent(ids[0])}`,
          )
        } else {
          await apiClient.post<{ executions: ExecutionView[] }>(
            '/api/api-testing/v1/executions/archive',
            { execution_ids: ids },
          )
        }
        const archived = new Set(ids)
        for (const id of ids) this.archivedExecutionIds.add(id)
        this.executions = this.executions.filter(item => !archived.has(item.id))
        if (archived.has(this.selectingExecutionId) || (this.active && archived.has(this.active.id))) {
          this.selectionVersion += 1
          this.selectingExecutionId = ''
        }
        if (this.active && archived.has(this.active.id)) {
          this.disconnect()
          this.active = null
          this.events = []
        }
      } catch (error) {
        this.error = error instanceof Error ? error.message : '执行记录删除失败'
        throw error
      } finally {
        this.deleting = false
      }
    },
    async rerunFailed(execution: ExecutionView): Promise<ExecutionView | null> {
      const caseIds = execution.case_results
        .filter(item => item.execution_role !== 'dependency' && !['PASSED', 'CANCELLED'].includes(item.status))
        .map(item => item.case_version_id)
      if (!caseIds.length) return null
      return await this.createRerun(execution, caseIds)
    },
    async rerunExecution(execution: ExecutionView): Promise<ExecutionView | null> {
      const caseIds = execution.case_results
        .filter(item => item.execution_role !== 'dependency')
        .map(item => item.case_version_id)
        .filter(Boolean)
      if (!caseIds.length) return null
      return await this.createRerun(execution, caseIds)
    },
    async createRerun(execution: ExecutionView, caseIds: string[]): Promise<ExecutionView> {
      const response = await apiClient.post<{ execution: ExecutionView }>(
        '/api/api-testing/v1/executions',
        {
          project_id: execution.project_id,
          source_revision_id: execution.source_revision_id,
          environment_revision_id: execution.environment_revision_id,
          case_version_ids: caseIds,
          execution_type: execution.execution_type,
          overrides: {},
          idempotency_key: createIdempotencyKey(),
        },
      )
      this.executions.unshift(response.data.execution)
      await this.select(response.data.execution.id)
      return response.data.execution
    },
    async runBaselines(input: { projectId: string; sourceRevisionId: string; environmentRevisionId: string; baselineIds?: string[] }): Promise<ExecutionView> {
      this.baselineStarting = true
      this.error = ''
      try {
        const payload: Record<string, unknown> = {
          project_id: input.projectId,
          source_revision_id: input.sourceRevisionId,
          environment_revision_id: input.environmentRevisionId,
          idempotency_key: createIdempotencyKey(),
        }
        const baselineIds = [...new Set(input.baselineIds || [])]
        if (baselineIds.length) payload.baseline_ids = baselineIds
        const response = await apiClient.post<{ execution: ExecutionView }>(
          '/api/api-testing/v1/regressions',
          payload,
        )
        this.executions.unshift(response.data.execution)
        await this.select(response.data.execution.id)
        return response.data.execution
      } catch (error) {
        this.error = error instanceof Error ? error.message : '基线回归创建失败'
        throw error
      } finally {
        this.baselineStarting = false
      }
    },
  },
})

function toEvent(id: number, type: string, payload: Record<string, unknown>): ExecutionEventView {
  const createdAt = typeof payload._event_created_at === 'string' ? payload._event_created_at : undefined
  const visiblePayload = { ...payload }
  delete visiblePayload._event_created_at
  const status = String(payload.status || payload.state || '')
  const level: ExecutionEventView['level'] =
    status === 'PASSED' ? 'success'
      : ['FAILED', 'BROKEN'].includes(status) || type === 'failure' ? 'error'
        : status === 'CANCELLED' ? 'warning' : 'info'
  const labels: Record<string, string> = {
    execution_queued: '任务已进入队列', execution_started: '开始执行', case_started: '开始执行用例',
    request: '发送请求', response: '收到响应', assertion: '执行断言', extraction: '提取变量',
    failure_analysis: 'AI 失败分析已生成', failure_analysis_unavailable: 'AI 失败分析暂不可用',
    case_finished: `用例完成${status ? `：${status}` : ''}`,
    cancellation_requested: '已请求取消', failure: '执行异常', execution_finished: `执行结束${status ? `：${status}` : ''}`,
    notification_sent: '飞书通知已发', notification_failed: '飞书通知发送失败',
  }
  return {
    id,
    type,
    level,
    caseId: String(payload.execution_case_id || ''),
    createdAt,
    message: labels[type] || String(payload.message || type),
    payload: visiblePayload,
  }
}

function mergeLoadedCaseEvidence(summary: ExecutionView, previous?: ExecutionView | null): ExecutionView {
  if (!previous || previous.id !== summary.id) return summary
  const loaded = new Map(
    previous.case_results
      .filter(item => item.evidence_loaded === true)
      .map(item => [item.execution_case_id, item]),
  )
  if (!loaded.size) return summary
  return {
    ...summary,
    case_results: summary.case_results.map(item => {
      const evidence = loaded.get(item.execution_case_id)
      if (!evidence) return item
      return {
        ...item,
        sanitized_result: evidence.sanitized_result,
        evidence_loaded: true,
        failure_analysis: item.failure_analysis || evidence.failure_analysis,
      }
    }),
  }
}

function replaceExecutionCase(execution: ExecutionView, result: ExecutionCaseResult): ExecutionView {
  return {
    ...execution,
    case_results: execution.case_results.map(item => (
      item.execution_case_id === result.execution_case_id ? result : item
    )),
  }
}
