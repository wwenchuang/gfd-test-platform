<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { AlertTriangle, ArrowLeft, BarChart3, CheckCircle2, Clock3, Eye, ListChecks, RefreshCw, Search, Send, Trash2 } from 'lucide-vue-next'

import DiagnosticReport from '../components/DiagnosticReport.vue'
import type { ExecutionCaseResult, ExecutionView } from '../api/contracts'
import {
  caseResultSummary,
  executionConclusion,
  executionDisplayName,
  executionFailureBuckets,
  executionMetrics,
  executionScopeLabel,
  executionSourceScope,
  formatDuration,
  formatPassRate,
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
const reportActionMessage = ref('')
const selectedReportIds = ref<Set<string>>(new Set())
const filter = ref<'all' | 'failed' | 'passed'>('all')
const sourceScope = ref<'formal' | 'debug' | 'all'>(defaultSourceScope(executions.executions))
const reportSearch = ref('')
const sendingReportId = ref('')
const openingDiagnosticId = ref('')
let diagnosticRequestVersion = 0
const diagnosticError = ref('')
const reportProjectId = ref('')
const mobileReportDetailOpen = ref(false)
const projectOptions = computed(() => context.projects)
const selectedProject = computed(() => projectOptions.value.find(item => item.id === reportProjectId.value) || null)
const reports = computed(() => executions.executions
  .filter(item => ['DONE', 'CANCELLED'].includes(item.state))
  .filter(item => !reportProjectId.value || item.project_id === reportProjectId.value))
const sourceScopedReports = computed(() => reports.value.filter(report => (
  sourceScope.value === 'all' || executionSourceScope(report) === sourceScope.value
)))
const visibleReports = computed(() => sourceScopedReports.value.filter(report => {
  const conclusion = executionConclusion(report)
  if (filter.value === 'failed' && !['failed', 'broken', 'cancelled', 'neutral'].includes(conclusion.tone)) return false
  if (filter.value === 'passed' && conclusion.tone !== 'passed') return false
  const keyword = reportSearch.value.trim().toLocaleLowerCase()
  if (!keyword) return true
  return [report.task_name, reportName(report), executionScopeLabel(report), report.environment_name, report.id]
    .join(' ')
    .toLocaleLowerCase()
    .includes(keyword)
}))
const dashboard = computed(() => {
  const aggregate = {
    totalReports: sourceScopedReports.value.length,
    totalCases: 0,
    passed: 0,
    failed: 0,
    broken: 0,
    skipped: 0,
    cancelled: 0,
    issueReports: 0,
    durationMs: 0,
  }
  for (const report of sourceScopedReports.value) {
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
    passRate: formatPassRate(aggregate.passed, aggregate.totalCases),
  }
})
const latestReport = computed(() => sourceScopedReports.value[0] || null)
const projectName = computed(() => selectedProject.value?.name || '未选择项目')
const reportRangeLabel = computed(() => sourceScopedReports.value.length ? `最近 ${sourceScopedReports.value.length} 次执行` : '暂无报告')
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
  reportProjectId.value = projectIdFromRoute() || context.projectId || context.projects[0]?.id || ''
  if (reportProjectId.value) await loadProjectReports(reportProjectId.value, true)
  if (reportIdFromRoute()) mobileReportDetailOpen.value = true
})

watch([visibleReports, () => route.query.execution_id, () => route.query.executionId], ([reports]) => {
  const visibleIds = new Set(reports.map(item => item.id))
  selectedReportIds.value = new Set([...selectedReportIds.value].filter(id => visibleIds.has(id)))
  const requested = reportIdFromRoute()
  if (requested && reports.some(item => item.id === requested)) {
    selectedReportId.value = requested
    mobileReportDetailOpen.value = true
  } else if (!reports.some(item => item.id === selectedReportId.value)) {
    selectedReportId.value = reports[0]?.id || ''
  }
}, { immediate: true })

watch([() => route.query.project_id, () => route.query.projectId], async () => {
  const requestedProjectId = projectIdFromRoute()
  if (!requestedProjectId || requestedProjectId === reportProjectId.value) return
  reportProjectId.value = requestedProjectId
  selected.value = null
  selectedReportId.value = ''
  selectedReportIds.value = new Set()
  filter.value = 'all'
  reportSearch.value = ''
  await loadProjectReports(requestedProjectId, true)
})

watch([selectedReportId, reportProjectId], () => {
  diagnosticRequestVersion += 1
  openingDiagnosticId.value = ''
}, { flush: 'sync' })

function projectIdFromRoute(): string {
  const value = route.query.project_id ?? route.query.projectId
  if (Array.isArray(value)) return String(value[0] || '')
  return String(value || '')
}

function reportIdFromRoute(): string {
  const value = route.query.execution_id ?? route.query.executionId
  if (Array.isArray(value)) return String(value[0] || '')
  return String(value || '')
}

async function loadProjectReports(projectId = reportProjectId.value, resetScope = false): Promise<void> {
  if (!projectId) {
    executions.executions = []
    selectedReportId.value = ''
    selectedReportIds.value = new Set()
    return
  }
  await Promise.all([executions.load(projectId), notifications.loadFeishu(projectId)])
  if (resetScope) sourceScope.value = defaultSourceScope(reports.value)
}

async function changeReportProject(event: Event): Promise<void> {
  const value = (event.target as HTMLSelectElement).value
  reportProjectId.value = value
  selected.value = null
  selectedReportId.value = ''
  selectedReportIds.value = new Set()
  filter.value = 'all'
  reportSearch.value = ''
  mobileReportDetailOpen.value = false
  await loadProjectReports(value, true)
}

function defaultSourceScope(items: ExecutionView[]): 'formal' | 'all' {
  return items.some(item => ['DONE', 'CANCELLED'].includes(item.state) && executionSourceScope(item) === 'formal')
    ? 'formal'
    : 'all'
}

function edit(result: ExecutionCaseResult, execution: ExecutionView): void {
  void router.push({ name: 'workbench', query: {
    endpointId: result.endpoint_id, caseVersionId: result.case_version_id,
    projectId: execution.project_id, sourceRevisionId: execution.source_revision_id,
    environmentRevisionId: execution.environment_revision_id,
  } })
}

function reportName(report: ExecutionView): string {
  return executionDisplayName(report)
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
  mobileReportDetailOpen.value = true
  replaceReportRoute(report.id)
}

async function openDiagnostic(report: ExecutionView): Promise<void> {
  if (executions.deleting) return
  selectReport(report)
  const requestVersion = ++diagnosticRequestVersion
  openingDiagnosticId.value = report.id
  diagnosticError.value = ''
  try {
    const detail = await executions.loadExecution(report.id)
    if (requestVersion !== diagnosticRequestVersion || executions.archivedExecutionIds.has(report.id)) return
    selected.value = detail
  } catch (error) {
    if (requestVersion !== diagnosticRequestVersion) return
    diagnosticError.value = error instanceof Error ? error.message : '无法读取完整诊断，请稍后重试'
  } finally {
    if (requestVersion === diagnosticRequestVersion) openingDiagnosticId.value = ''
  }
}

async function loadDiagnosticEvidence(result: ExecutionCaseResult): Promise<void> {
  const reportId = selected.value?.id
  if (!reportId) return
  try {
    const loaded = await executions.loadExecutionCase(reportId, result.execution_case_id)
    if (selected.value?.id !== reportId) return
    selected.value = {
      ...selected.value,
      case_results: selected.value.case_results.map(item => (
        item.execution_case_id === loaded.execution_case_id ? loaded : item
      )),
    }
  } catch {
    // The selected report keeps a visible retry action beside the evidence pane.
  }
}

function showReportList(): void {
  mobileReportDetailOpen.value = false
  replaceReportRoute('')
}

function replaceReportRoute(reportId: string): void {
  const query = { ...route.query }
  delete query.executionId
  delete query.execution_id
  if (reportId) query.executionId = reportId
  void router.replace({ query })
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
  if (executions.deleting) return
  const ids = [...new Set(reportIds)].filter(Boolean)
  if (!ids.length) return
  const names = executions.executions.filter(item => ids.includes(item.id)).slice(0, 3).map(executionDisplayName).join('、')
  if (!window.confirm(`确认删除 ${ids.length} 条报告及对应执行记录？${names ? `\n${names}${ids.length > 3 ? ' 等' : ''}` : ''}\n这些记录将从列表移除，不会删除用例或基线。`)) return
  if (ids.includes(openingDiagnosticId.value)) {
    diagnosticRequestVersion += 1
    openingDiagnosticId.value = ''
  }
  reportActionMessage.value = ''
  try {
    await executions.deleteExecutions(ids)
    selectedReportIds.value = new Set([...selectedReportIds.value].filter(id => !ids.includes(id)))
    if (selected.value && ids.includes(selected.value.id)) selected.value = null
    if (ids.includes(selectedReportId.value)) selectedReportId.value = visibleReports.value[0]?.id || ''
    const linkedId = String(route.query.executionId || route.query.execution_id || '')
    if (ids.includes(linkedId)) replaceReportRoute(currentReport.value?.id || '')
    reportActionMessage.value = `已删除 ${ids.length} 条报告及对应执行记录，用例和基线仍保留。`
  } catch (error) {
    executions.error = error instanceof Error ? error.message : '报告删除失败，请刷新列表核对后重试'
  }
}

</script>

<template>
  <section class="workspace">
    <template v-if="selected"><DiagnosticReport :execution="selected" :loading-case-keys="executions.loadingCaseKeys" :case-evidence-errors="executions.caseEvidenceErrors" @load-evidence="loadDiagnosticEvidence" @back="selected = null" @edit="edit" @rerun="executions.rerunFailed($event)" /></template>
    <template v-else>
      <header class="page-toolbar">
        <div>
          <p class="eyebrow">测试报告</p>
          <h1>项目测试报告</h1>
          <p class="page-subtitle">按项目查看接口自动化结果。先看整体结论，再定位问题报告和用例证据。</p>
        </div>
      </header>

      <section class="report-project-scope" aria-label="项目报告范围">
        <div class="report-project-identity">
          <BarChart3 :size="18" />
          <div>
            <span>项目报告</span>
            <strong>{{ projectName }}</strong>
            <small>{{ reportRangeLabel }} · 只展示当前项目执行结果</small>
          </div>
        </div>
        <div class="report-project-controls">
          <label>
            项目
            <select data-testid="report-project-select" :value="reportProjectId" @change="changeReportProject">
              <option value="" disabled>请选择项目</option>
              <option v-for="project in projectOptions" :key="project.id" :value="project.id">{{ project.name }}</option>
            </select>
          </label>
          <button type="button" class="secondary-command" :disabled="!reportProjectId || executions.loading" @click="loadProjectReports()">
            <RefreshCw :size="15" :class="{ 'is-spinning': executions.loading }" />刷新报告
          </button>
        </div>
      </section>

      <section class="report-dashboard" aria-label="报告概览">
        <div class="report-hero">
          <div>
            <p class="eyebrow">项目报告驾驶舱</p>
            <h2>{{ dashboard.issueCases ? '需要关注' : '全部通过' }}</h2>
            <p>{{ dashboard.totalReports }} 次执行 · {{ dashboard.totalCases }} 条用例 · 最近 {{ latestReport ? new Date(latestReport.created_at).toLocaleString('zh-CN') : '暂无执行' }}</p>
          </div>
          <strong data-testid="report-dashboard-rate" :class="dashboard.issueCases ? 'tone-failed' : 'tone-passed'">{{ dashboard.passRate }}</strong>
        </div>
        <div class="report-stat-grid">
          <div><ListChecks :size="16" /><span>执行次数</span><strong data-testid="report-dashboard-total">{{ dashboard.totalReports }} 次执行</strong></div>
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
      <p v-if="executions.error" class="inline-error" role="alert">{{ executions.error }}</p>
      <p v-if="executions.deleting || reportActionMessage" class="setup-success" role="status">{{ executions.deleting ? '正在删除选中的报告，请等待结果…' : reportActionMessage }}</p>
      <p v-if="notifications.error" class="inline-error">{{ notifications.error }}</p>
      <p v-if="notifications.lastSendMessage" class="setup-success"><Send :size="16" />{{ notifications.lastSendMessage }}</p>

      <section class="report-board">
        <header class="report-board-header">
          <div>
            <BarChart3 :size="17" />
            <strong>执行报告</strong>
            <span>{{ visibleReports.length }} / {{ sourceScopedReports.length }}</span>
          </div>
          <div class="report-board-actions">
            <div class="report-source-tabs" aria-label="报告来源范围">
              <button data-testid="report-source-formal" type="button" :class="{ active: sourceScope === 'formal' }" :aria-pressed="sourceScope === 'formal'" @click="sourceScope = 'formal'">正式回归</button>
              <button data-testid="report-source-debug" type="button" :class="{ active: sourceScope === 'debug' }" :aria-pressed="sourceScope === 'debug'" @click="sourceScope = 'debug'">在线调试</button>
              <button data-testid="report-source-all" type="button" :class="{ active: sourceScope === 'all' }" :aria-pressed="sourceScope === 'all'" @click="sourceScope = 'all'">全部记录</button>
            </div>
            <div class="report-filter-tabs" aria-label="报告筛选">
              <button type="button" :class="{ active: filter === 'all' }" @click="filter = 'all'">全部</button>
              <button type="button" :class="{ active: filter === 'failed' }" @click="filter = 'failed'">有问题</button>
              <button data-testid="report-filter-passed" type="button" :class="{ active: filter === 'passed' }" @click="filter = 'passed'">已通过</button>
            </div>
            <button type="button" class="danger-command" :disabled="executions.deleting || !selectedReportCount" @click="deleteReports([...selectedReportIds])"><Trash2 :size="14" />批量删除 {{ selectedReportCount || '' }}</button>
          </div>
        </header>
        <div data-testid="report-workbench" :class="['report-workbench', { 'mobile-detail-open': mobileReportDetailOpen }]">
          <aside class="report-index">
            <label class="search-box report-search"><Search :size="14" /><span class="sr-only">搜索报告</span><input v-model="reportSearch" data-testid="report-search" placeholder="搜索任务或环境" /></label>
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
              <span class="sr-only" :data-testid="`report-history-row-${report.id}`">{{ report.id }}</span>
              <input type="checkbox" :checked="selectedReportIds.has(report.id)" aria-label="选择报告" @click.stop="toggleReportSelection(report.id)" />
              <div>
                <span class="report-status-chip">{{ executionConclusion(report).label }}</span>
                <strong>{{ reportName(report) }}</strong>
                <time>{{ new Date(report.created_at).toLocaleString('zh-CN') }}</time>
                <small>{{ executionScopeLabel(report) }} · {{ report.environment_name || '未命名环境' }} · {{ executionMetrics(report).total }} 条用例</small>
              </div>
              <b>{{ executionMetrics(report).passRate }}%</b>
              <button type="button" class="icon-danger" aria-label="删除报告" :disabled="executions.deleting" @click.stop="deleteReports([report.id])"><Trash2 :size="13" /></button>
            </article>
            <div v-if="!visibleReports.length" class="section-empty">暂无匹配报告。</div>
          </aside>

          <main class="report-detail-panel">
            <button data-testid="report-back-to-list" class="management-back-to-list report-back-to-list" type="button" @click="showReportList"><ArrowLeft :size="16" />返回报告列表</button>
            <template v-if="currentReport && currentMetrics && currentBuckets">
              <header class="report-detail-hero" :class="`tone-${executionConclusion(currentReport).tone}`">
                <div>
                  <span class="report-status-chip">{{ executionConclusion(currentReport).label }}</span>
                  <h2>{{ reportName(currentReport) }}</h2>
                  <p>{{ executionScopeLabel(currentReport) }} · {{ currentReport.environment_name || '未命名环境' }} · {{ new Date(currentReport.created_at).toLocaleString('zh-CN') }}</p>
                </div>
                <strong>{{ currentMetrics.passRate }}%</strong>
              </header>
              <div class="report-detail-actions">
                <button
                  data-testid="report-open-diagnostic"
                  type="button"
                  class="secondary-command"
                  :disabled="openingDiagnosticId === currentReport.id"
                  @click="openDiagnostic(currentReport)"
                ><Eye :size="14" />{{ openingDiagnosticId === currentReport.id ? '读取诊断中' : '查看完整诊断' }}</button>
                <button
                  data-testid="report-feishu-status"
                  type="button"
                  :class="['secondary-command', 'report-send-command', `feishu-${feishuReportState(currentReport).tone}`]"
                  :disabled="notifications.sending && sendingReportId === currentReport.id"
                  @click="sendFeishu(currentReport)"
                >
                  <Send :size="13" />{{ sendingReportId === currentReport.id ? '发送中' : feishuReportState(currentReport).label }}
                </button>
                <button type="button" class="danger-command" :disabled="executions.deleting" @click="deleteReports([currentReport.id])"><Trash2 :size="14" />删除报告</button>
              </div>
              <p v-if="diagnosticError" class="inline-error" role="alert">{{ diagnosticError }}</p>
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
            <div v-else class="section-empty">{{ reports.length ? '暂无匹配报告。请清空搜索或切换筛选查看已有记录。' : '暂无报告。执行接口或基线回归后，这里会展示报告摘要。' }}</div>
          </main>
        </div>
      </section>
    </template>
  </section>
</template>
