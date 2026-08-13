<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { AlertTriangle, BarChart3, CheckCircle2, Clock3, Eye, ListChecks, Send, Trash2 } from 'lucide-vue-next'

import DiagnosticReport from '../components/DiagnosticReport.vue'
import type { ExecutionCaseResult, ExecutionView } from '../api/contracts'
import {
  caseResultSummary,
  executionConclusion,
  executionFailureBuckets,
  executionMetrics,
  executionTypeLabel,
  formatDuration,
  statusLabel,
} from '../utils/executionPresentation'
import { useContextStore } from '../stores/context'
import { useExecutionsStore } from '../stores/executions'
import { useNotificationsStore } from '../stores/notifications'

const context = useContextStore()
const executions = useExecutionsStore()
const notifications = useNotificationsStore()
const router = useRouter()
const route = useRoute()
const selected = ref<ExecutionView | null>(null)
const selectedReportId = ref('')
const selectedReportIds = ref<Set<string>>(new Set())
const filter = ref<'all' | 'failed' | 'passed'>('all')
const sendingReportId = ref('')
const reports = computed(() => executions.executions.filter(item => ['DONE', 'CANCELLED'].includes(item.state)))
const visibleReports = computed(() => reports.value.filter(report => {
  const conclusion = executionConclusion(report)
  if (filter.value === 'failed') return ['failed', 'broken', 'cancelled', 'neutral'].includes(conclusion.tone)
  if (filter.value === 'passed') return conclusion.tone === 'passed'
  return true
}))
const dashboard = computed(() => {
  const aggregate = {
    totalReports: reports.value.length,
    totalCases: 0,
    passed: 0,
    failed: 0,
    broken: 0,
    skipped: 0,
    cancelled: 0,
    issueReports: 0,
    durationMs: 0,
  }
  for (const report of reports.value) {
    const metrics = executionMetrics(report)
    const conclusion = executionConclusion(report)
    aggregate.totalCases += metrics.total
    aggregate.passed += metrics.passed
    aggregate.failed += metrics.failed
    aggregate.broken += metrics.broken
    aggregate.skipped += metrics.skipped
    aggregate.cancelled += metrics.cancelled
    aggregate.durationMs += metrics.durationMs
    if (conclusion.tone !== 'passed') aggregate.issueReports += 1
  }
  const issueCases = aggregate.failed + aggregate.broken + aggregate.skipped + aggregate.cancelled
  return {
    ...aggregate,
    issueCases,
    passRate: aggregate.totalCases ? Math.round((aggregate.passed / aggregate.totalCases) * 100) : 0,
  }
})
const latestReport = computed(() => reports.value[0] || null)
const projectName = computed(() => context.projects.find(item => item.id === context.projectId)?.name || '未选择项目')
const currentReport = computed(() => visibleReports.value.find(item => item.id === selectedReportId.value) || visibleReports.value[0] || null)
const currentMetrics = computed(() => currentReport.value ? executionMetrics(currentReport.value) : null)
const currentBuckets = computed(() => currentReport.value ? executionFailureBuckets(currentReport.value) : null)
const currentIssueResults = computed(() => {
  const report = currentReport.value
  if (!report) return []
  const issues = report.case_results.filter(item => item.status !== 'PASSED')
  return (issues.length ? issues : report.case_results).slice(0, 8)
})
const selectedReportCount = computed(() => selectedReportIds.value.size)
const allVisibleReportsSelected = computed(() => visibleReports.value.length > 0 && visibleReports.value.every(item => selectedReportIds.value.has(item.id)))

type FeishuReportState = {
  label: string
  tone: 'idle' | 'sent' | 'failed'
}

onMounted(async () => {
  await Promise.all([context.loadSavedContext(), context.loadOptions()])
  if (context.projectId) await Promise.all([executions.load(context.projectId), notifications.loadFeishu(context.projectId)])
})

watch([visibleReports, () => route.query.execution_id, () => route.query.executionId], ([reports]) => {
  const visibleIds = new Set(reports.map(item => item.id))
  selectedReportIds.value = new Set([...selectedReportIds.value].filter(id => visibleIds.has(id)))
  const requested = reportIdFromRoute()
  if (requested && reports.some(item => item.id === requested)) {
    selectedReportId.value = requested
  } else if (!reports.some(item => item.id === selectedReportId.value)) {
    selectedReportId.value = reports[0]?.id || ''
  }
}, { immediate: true })

function reportIdFromRoute(): string {
  const value = route.query.execution_id ?? route.query.executionId
  if (Array.isArray(value)) return String(value[0] || '')
  return String(value || '')
}

function edit(result: ExecutionCaseResult, execution: ExecutionView): void {
  void router.push({ name: 'workbench', query: {
    endpointId: result.endpoint_id, caseVersionId: result.case_version_id,
    projectId: execution.project_id, sourceRevisionId: execution.source_revision_id,
    environmentRevisionId: execution.environment_revision_id,
  } })
}

function reportName(report: ExecutionView): string {
  return executionTypeLabel(report)
}

function feishuReportState(report: ExecutionView): FeishuReportState {
  const status = report.notifications?.feishu
  if (status?.sent) return { label: status.message || '飞书通知已发', tone: 'sent' }
  if (status?.failed) return { label: status.message || '飞书通知发送失败', tone: 'failed' }
  return { label: '发飞书', tone: 'idle' }
}

function markFeishuSent(reportId: string, message: string): void {
  const status = { sent: true, failed: false, message: message || '飞书通知已发' }
  const report = executions.executions.find(item => item.id === reportId)
  if (report) report.notifications = { ...(report.notifications || {}), feishu: status }
  if (selected.value?.id === reportId) {
    selected.value.notifications = { ...(selected.value.notifications || {}), feishu: status }
  }
}

async function sendFeishu(report: ExecutionView): Promise<void> {
  sendingReportId.value = report.id
  try {
    const result = await notifications.sendExecutionReport(report.id)
    markFeishuSent(report.id, result.message)
  } finally {
    sendingReportId.value = ''
  }
}

function selectReport(report: ExecutionView): void {
  selectedReportId.value = report.id
}

function toggleReportSelection(reportId: string): void {
  const next = new Set(selectedReportIds.value)
  if (next.has(reportId)) next.delete(reportId)
  else next.add(reportId)
  selectedReportIds.value = next
}

function toggleAllReports(): void {
  selectedReportIds.value = allVisibleReportsSelected.value
    ? new Set()
    : new Set(visibleReports.value.map(item => item.id))
}

async function deleteReports(reportIds: string[]): Promise<void> {
  const ids = [...new Set(reportIds)].filter(Boolean)
  if (!ids.length) return
  await executions.deleteExecutions(ids)
  selectedReportIds.value = new Set()
  if (selected.value && ids.includes(selected.value.id)) selected.value = null
  if (ids.includes(selectedReportId.value)) {
    selectedReportId.value = visibleReports.value.find(item => !ids.includes(item.id))?.id || ''
  }
}
</script>

<template>
  <section class="workspace">
    <template v-if="selected"><DiagnosticReport :execution="selected" @back="selected = null" @edit="edit" @rerun="executions.rerunFailed($event)" /></template>
    <template v-else>
      <header class="page-toolbar">
        <div>
          <p class="eyebrow">API TEST REPORTS</p>
          <h1>测试报告</h1>
          <p class="page-subtitle">当前项目：{{ projectName }}。先看结论、问题分布和失败摘要，再按用例展开请求、响应与断言证据。</p>
        </div>
      </header>

      <section class="report-dashboard" aria-label="报告概览">
        <div class="report-hero">
          <div>
            <p class="eyebrow">报告概览</p>
            <h2>{{ dashboard.issueCases ? '需要关注' : '全部通过' }}</h2>
            <p>{{ dashboard.totalReports }} 次执行 · {{ dashboard.totalCases }} 条用例 · 最近 {{ latestReport ? new Date(latestReport.created_at).toLocaleString('zh-CN') : '暂无执行' }}</p>
          </div>
          <strong :class="dashboard.issueCases ? 'tone-failed' : 'tone-passed'">{{ dashboard.passRate }}%</strong>
        </div>
        <div class="report-stat-grid">
          <div><ListChecks :size="16" /><span>执行次数</span><strong>{{ dashboard.totalReports }} 次执行</strong></div>
          <div><CheckCircle2 :size="16" /><span>通过用例</span><strong>{{ dashboard.passed }}</strong></div>
          <div><AlertTriangle :size="16" /><span>问题用例</span><strong>{{ dashboard.issueCases }} 个问题</strong></div>
          <div><Clock3 :size="16" /><span>累计耗时</span><strong>{{ formatDuration(dashboard.durationMs) }}</strong></div>
        </div>
        <div class="report-bucket-bar">
          <span :style="{ flexGrow: Math.max(dashboard.passed, 1) }" class="bucket-passed">通过 {{ dashboard.passed }}</span>
          <span :style="{ flexGrow: Math.max(dashboard.failed, 0) }" class="bucket-failed">失败 {{ dashboard.failed }}</span>
          <span :style="{ flexGrow: Math.max(dashboard.broken, 0) }" class="bucket-broken">异常 {{ dashboard.broken }}</span>
          <span :style="{ flexGrow: Math.max(dashboard.skipped + dashboard.cancelled, 0) }" class="bucket-neutral">未完成 {{ dashboard.skipped + dashboard.cancelled }}</span>
        </div>
      </section>
      <p v-if="notifications.error" class="inline-error">{{ notifications.error }}</p>
      <p v-if="notifications.lastSendMessage" class="setup-success"><Send :size="16" />{{ notifications.lastSendMessage }}</p>

      <section class="report-board">
        <header class="report-board-header">
          <div>
            <BarChart3 :size="17" />
            <strong>执行报告</strong>
            <span>{{ visibleReports.length }} / {{ reports.length }}</span>
          </div>
          <div class="report-board-actions">
            <div class="report-filter-tabs" aria-label="报告筛选">
              <button type="button" :class="{ active: filter === 'all' }" @click="filter = 'all'">全部</button>
              <button type="button" :class="{ active: filter === 'failed' }" @click="filter = 'failed'">有问题</button>
              <button type="button" :class="{ active: filter === 'passed' }" @click="filter = 'passed'">已通过</button>
            </div>
            <button type="button" class="danger-command" :disabled="!selectedReportCount" @click="deleteReports([...selectedReportIds])"><Trash2 :size="14" />批量删除 {{ selectedReportCount || '' }}</button>
          </div>
        </header>
        <div class="report-workbench">
          <aside class="report-index">
            <div class="report-index-tools">
              <button type="button" class="text-command" :disabled="!visibleReports.length" @click="toggleAllReports">{{ allVisibleReportsSelected ? '取消全选' : '全选当前筛选' }}</button>
              <span>已选 {{ selectedReportCount }} 条</span>
            </div>
            <article
              v-for="report in visibleReports"
              :key="report.id"
              data-testid="report-history-row"
              role="button"
              tabindex="0"
              :class="['report-index-row', `tone-${executionConclusion(report).tone}`, { active: report.id === currentReport?.id }]"
              @click="selectReport(report)"
              @keydown.enter="selectReport(report)"
            >
              <input type="checkbox" :checked="selectedReportIds.has(report.id)" aria-label="选择报告" @click.stop="toggleReportSelection(report.id)" />
              <div>
                <span class="report-status-chip">{{ executionConclusion(report).label }}</span>
                <strong>{{ reportName(report) }}</strong>
                <time>{{ new Date(report.created_at).toLocaleString('zh-CN') }}</time>
                <small>{{ report.environment_name || '未命名环境' }} · {{ executionMetrics(report).total }} 条用例</small>
              </div>
              <b>{{ executionMetrics(report).passRate }}%</b>
              <button type="button" class="icon-danger" aria-label="删除报告" @click.stop="deleteReports([report.id])"><Trash2 :size="13" /></button>
            </article>
            <div v-if="!visibleReports.length" class="section-empty">暂无匹配报告。</div>
          </aside>

          <main class="report-detail-panel">
            <template v-if="currentReport && currentMetrics && currentBuckets">
              <header class="report-detail-hero" :class="`tone-${executionConclusion(currentReport).tone}`">
                <div>
                  <span class="report-status-chip">{{ executionConclusion(currentReport).label }}</span>
                  <h2>{{ reportName(currentReport) }}</h2>
                  <p>{{ currentReport.environment_name || '未命名环境' }} · {{ new Date(currentReport.created_at).toLocaleString('zh-CN') }}</p>
                </div>
                <strong>{{ currentMetrics.passRate }}%</strong>
              </header>
              <div class="report-detail-actions">
                <button data-testid="report-open-diagnostic" type="button" class="secondary-command" @click="selected = currentReport"><Eye :size="14" />查看完整诊断</button>
                <button
                  data-testid="report-feishu-status"
                  type="button"
                  :class="['secondary-command', 'report-send-command', `feishu-${feishuReportState(currentReport).tone}`]"
                  :disabled="notifications.sending && sendingReportId === currentReport.id"
                  @click="sendFeishu(currentReport)"
                >
                  <Send :size="13" />{{ sendingReportId === currentReport.id ? '发送中' : feishuReportState(currentReport).label }}
                </button>
                <button type="button" class="danger-command" @click="deleteReports([currentReport.id])"><Trash2 :size="14" />删除报告</button>
              </div>
              <div class="report-detail-stats">
                <div><span>总用例</span><strong>{{ currentMetrics.total }}</strong></div>
                <div><span>通过</span><strong class="tone-passed">{{ currentMetrics.passed }}</strong></div>
                <div><span>失败</span><strong class="tone-failed">{{ currentMetrics.failed }}</strong></div>
                <div><span>异常</span><strong class="tone-broken">{{ currentMetrics.broken }}</strong></div>
                <div><span>耗时</span><strong>{{ formatDuration(currentMetrics.durationMs) }}</strong></div>
              </div>
              <section class="report-detail-buckets">
                <div><strong>产品失败 {{ currentBuckets.product }}</strong><span>后端业务码、响应字段或产品断言不符合预期</span></div>
                <div><strong>脚本/数据 {{ currentBuckets.scriptData }}</strong><span>测试数据缺失、参数模板或脚本配置问题</span></div>
                <div><strong>环境异常 {{ currentBuckets.environment }}</strong><span>环境、网络、鉴权配置或服务不可用</span></div>
              </section>
              <section class="report-detail-evidence">
                <header><strong>{{ currentMetrics.failed + currentMetrics.broken + currentMetrics.skipped + currentMetrics.cancelled ? '问题证据' : '通过证据' }}</strong><span>{{ currentIssueResults.length }} 条</span></header>
                <article v-for="result in currentIssueResults" :key="result.execution_case_id">
                  <b :class="`status-${String(result.status).toLowerCase()}`">{{ statusLabel(result.status) }}</b>
                  <div>
                    <strong>{{ result.case_name || result.endpoint_summary || result.path }}</strong>
                    <small>{{ result.method }} {{ result.path }}</small>
                  </div>
                  <p>{{ caseResultSummary(result) }}</p>
                </article>
              </section>
            </template>
            <div v-else class="section-empty">暂无报告。执行接口或基线回归后，这里会展示报告摘要。</div>
          </main>
        </div>
      </section>
    </template>
  </section>
</template>
