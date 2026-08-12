<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { AlertTriangle, BarChart3, CheckCircle2, Clock3, ListChecks, Send } from 'lucide-vue-next'

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
const selected = ref<ExecutionView | null>(null)
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

onMounted(async () => {
  await Promise.all([context.loadSavedContext(), context.loadOptions()])
  if (context.projectId) await Promise.all([executions.load(context.projectId), notifications.loadFeishu(context.projectId)])
})

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

function importantResults(report: ExecutionView): ExecutionCaseResult[] {
  const issues = report.case_results.filter(item => item.status !== 'PASSED')
  return (issues.length ? issues : report.case_results).slice(0, 3)
}

async function sendFeishu(report: ExecutionView): Promise<void> {
  sendingReportId.value = report.id
  try {
    await notifications.sendExecutionReport(report.id)
  } finally {
    sendingReportId.value = ''
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
          <div class="report-filter-tabs" aria-label="报告筛选">
            <button type="button" :class="{ active: filter === 'all' }" @click="filter = 'all'">全部</button>
            <button type="button" :class="{ active: filter === 'failed' }" @click="filter = 'failed'">有问题</button>
            <button type="button" :class="{ active: filter === 'passed' }" @click="filter = 'passed'">已通过</button>
          </div>
        </header>
        <div class="report-list">
          <button
            v-for="report in visibleReports"
            :key="report.id"
            data-testid="report-history-row"
            type="button"
            :class="['report-history-card', `tone-${executionConclusion(report).tone}`]"
            @click="selected = report"
          >
            <div class="report-card-main">
              <span class="report-status-chip">{{ executionConclusion(report).label }}</span>
              <strong>{{ reportName(report) }}</strong>
              <time>{{ new Date(report.created_at).toLocaleString('zh-CN') }}</time>
              <small>{{ report.environment_name || '未命名环境' }}</small>
              <button class="secondary-command report-send-command" type="button" :disabled="notifications.sending && sendingReportId === report.id" @click.stop="sendFeishu(report)"><Send :size="13" />{{ sendingReportId === report.id ? '发送中' : '发飞书' }}</button>
            </div>
            <div class="report-card-metrics">
              <div><strong>通过率 {{ executionMetrics(report).passRate }}%</strong><span>真实通过 / 总用例</span></div>
              <div><strong>{{ executionMetrics(report).total }} 条</strong><span>总用例</span></div>
              <div><strong>{{ formatDuration(executionMetrics(report).durationMs) }}</strong><span>耗时</span></div>
            </div>
            <div class="report-card-buckets">
              <span>产品失败 {{ executionFailureBuckets(report).product }}</span>
              <span>脚本/数据 {{ executionFailureBuckets(report).scriptData }}</span>
              <span>环境异常 {{ executionFailureBuckets(report).environment }}</span>
            </div>
            <div class="report-card-evidence">
              <strong>{{ executionMetrics(report).failed + executionMetrics(report).broken + executionMetrics(report).skipped + executionMetrics(report).cancelled ? '关键问题' : '通过证据' }}</strong>
              <p v-for="result in importantResults(report)" :key="result.execution_case_id">
                <b>{{ statusLabel(result.status) }}</b>
                <span>{{ result.case_name || result.endpoint_summary || result.path }}</span>
                <small>{{ caseResultSummary(result) }}</small>
              </p>
            </div>
          </button>
          <div v-if="!visibleReports.length" class="section-empty">暂无匹配报告。</div>
        </div>
      </section>
    </template>
  </section>
</template>
