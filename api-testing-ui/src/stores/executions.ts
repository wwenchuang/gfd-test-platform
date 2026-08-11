import { defineStore } from 'pinia'
import { markRaw } from 'vue'

import { apiClient } from '../api/client'
import type { ExecutionConnectionState, ExecutionEventView, ExecutionView } from '../api/contracts'

const TERMINAL = new Set(['DONE', 'CANCELLED', 'PASSED', 'FAILED', 'BROKEN'])
const EVENT_TYPES = [
  'execution_queued', 'execution_started', 'case_started', 'request', 'response',
  'assertion', 'extraction', 'failure_analysis', 'failure_analysis_unavailable', 'case_finished', 'cancellation_requested', 'failure', 'execution_finished',
]
const RECONNECT_DELAYS_MS = [1000, 2000, 5000, 10000, 30000]
const ANALYSIS_REFRESH_DELAYS_MS = [500, 1000, 2000, 4000, 8000, 10000]

export const useExecutionsStore = defineStore('api-executions', {
  state: () => ({
    executions: [] as ExecutionView[],
    active: null as ExecutionView | null,
    events: [] as ExecutionEventView[],
    connectionState: 'idle' as ExecutionConnectionState,
    loading: false,
    baselineStarting: false,
    error: '',
    eventSource: null as EventSource | null,
    reconnectTimer: null as ReturnType<typeof setTimeout> | null,
    reconnectAttempts: 0,
    selectionVersion: 0,
    analysisRefreshTimer: null as ReturnType<typeof setTimeout> | null,
    analysisRefreshAttempts: 0,
  }),
  actions: {
    async load(projectId: string): Promise<void> {
      this.loading = true
      this.error = ''
      try {
        const response = await apiClient.get<{ executions: ExecutionView[] }>(
          `/api/api-testing/v1/executions?project_id=${encodeURIComponent(projectId)}&limit=50`,
        )
        this.executions = response.data.executions
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
      if (this.active && this.active.id !== executionId) return response.data.execution
      this.active = response.data.execution
      const index = this.executions.findIndex(item => item.id === executionId)
      if (index >= 0) this.executions[index] = this.active
      else this.executions.unshift(this.active)
      return this.active
    },
    async select(executionId: string): Promise<void> {
      const selectionVersion = ++this.selectionVersion
      const changed = this.active?.id !== executionId
      if (changed) {
        this.disconnect()
        this.events = []
      }
      this.reconnectAttempts = 0
      const response = await apiClient.get<{ execution: ExecutionView }>(
        `/api/api-testing/v1/executions/${executionId}`,
      )
      if (selectionVersion !== this.selectionVersion) return
      const execution = response.data.execution
      this.active = execution
      const index = this.executions.findIndex(item => item.id === executionId)
      if (index >= 0) this.executions[index] = execution
      else this.executions.unshift(execution)
      await this.connect(executionId)
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
      this.reconnectAttempts = 0
      this.error = ''
      await this.connect(executionId)
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
    async rerunFailed(execution: ExecutionView): Promise<ExecutionView | null> {
      const caseIds = execution.case_results
        .filter(item => !['PASSED', 'SKIPPED', 'CANCELLED'].includes(item.status))
        .map(item => item.case_version_id)
      if (!caseIds.length) return null
      const response = await apiClient.post<{ execution: ExecutionView }>(
        '/api/api-testing/v1/executions',
        {
          project_id: execution.project_id,
          source_revision_id: execution.source_revision_id,
          environment_revision_id: execution.environment_revision_id,
          case_version_ids: caseIds,
          execution_type: execution.execution_type,
          overrides: {},
          idempotency_key: crypto.randomUUID(),
        },
      )
      this.executions.unshift(response.data.execution)
      await this.select(response.data.execution.id)
      return response.data.execution
    },
    async runBaselines(input: { projectId: string; sourceRevisionId: string; environmentRevisionId: string }): Promise<ExecutionView> {
      this.baselineStarting = true
      this.error = ''
      try {
        const response = await apiClient.post<{ execution: ExecutionView }>(
          '/api/api-testing/v1/regressions',
          {
            project_id: input.projectId,
            source_revision_id: input.sourceRevisionId,
            environment_revision_id: input.environmentRevisionId,
            idempotency_key: crypto.randomUUID(),
          },
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
  }
  return {
    id,
    type,
    level,
    caseId: String(payload.execution_case_id || ''),
    message: labels[type] || String(payload.message || type),
    payload,
  }
}
