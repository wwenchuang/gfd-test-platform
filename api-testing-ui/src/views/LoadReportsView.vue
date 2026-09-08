<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { Bell, ChevronDown, History, RefreshCw } from 'lucide-vue-next'
import { useRoute, useRouter } from 'vue-router'
import LoadAiAnalysis from '../components/LoadAiAnalysis.vue'
import LoadMetricChart from '../components/LoadMetricChart.vue'
import LoadResourceMonitoring from '../components/LoadResourceMonitoring.vue'
import LoadRunConsole from '../components/LoadRunConsole.vue'
import type { LoadAiAnalysis as Analysis, LoadReport } from '../api/contracts'
import { useContextStore } from '../stores/context'
import { useLoadTestingStore } from '../stores/loadTesting'
import { apiTestingHasPermission } from '../utils/authRedirect'

const route = useRoute()
const router = useRouter()
const context = useContextStore()
const store = useLoadTestingStore()
const runId = ref('')
const report = ref<LoadReport | null>(null)
const analysis = ref<Analysis | null>(null)
const loading = ref(false)
const analyzing = ref(false)
const feedback = ref('')
const runQuery = ref('')
const runState = ref('all')
const applicationFilter = ref('')
const historyOpen = ref(false)
const historyLimit = ref(8)
const canNotify = apiTestingHasPermission('platform.notify')
const selectedRun = computed(() => store.runs.find(item => item.id === runId.value) || null)
const monitoring = computed(() => report.value?.monitoring || selectedRun.value?.summary?.monitoring as Record<string, unknown> | undefined)
const terminal = computed(() => selectedRun.value ? ['finished', 'failed', 'cancelled'].includes(selectedRun.value.state) : false)
const hasRequests = computed(() => Number(report.value?.transport.requests || 0) > 0)
const percentileLabels = [
  { key: 'p50_ms', name: 'P50 · 中位耗时', description: '50% 的请求不超过此值' },
  { key: 'p90_ms', name: 'P90 · 九成请求', description: '90% 的请求不超过此值' },
  { key: 'p95_ms', name: 'P95 · 重点关注', description: '95% 的请求不超过此值' },
  { key: 'p99_ms', name: 'P99 · 尾部慢请求', description: '99% 的请求不超过此值' },
  { key: 'max_ms', name: '最大耗时', description: '本次最慢的一次请求' },
]
function latency(key: string): string {
  const value = report.value?.latency[key]
  return !hasRequests.value || value == null || !Number.isFinite(Number(value)) ? '—' : Number(value).toLocaleString('zh-CN', { maximumFractionDigits: 2 })
}
const sampleIntegrity = computed(() => report.value?.evidence.sample_integrity)
const evidenceDescription = computed(() => {
  const evidence = report.value?.evidence
  if (!evidence) return '尚未收到完整性检查结果'
  const reasons: string[] = []
  if (sampleIntegrity.value?.consistent === false) reasons.push(sampleIntegrity.value.acceptable ? '少量采样偏差（容差内）' : '采样计数不一致')
  if (Number(evidence.missing_windows || 0) > 0) reasons.push(`${evidence.missing_windows} 个时段缺失`)
  if (Number(evidence.finished_shards || 0) < Number(evidence.total_shards || 0)) reasons.push('部分节点未正常完成')
  if (reasons.length) return reasons.join(' · ')
  if (evidence.complete === true) return sampleIntegrity.value?.consistent === true ? '全部节点已完成，采样计数一致' : '全部节点已完成；历史报告未记录采样计数校验'
  return evidence.complete === false ? '证据未通过完整性检查 · 请查看原因和节点明细' : '历史报告未记录完整性检查结果'
})
function integrityLocation(shardId: string, stepId: string): string {
  const agent = report.value?.agents?.find(item => item.shard_id === shardId) || report.value?.agents?.find(item => item.id === shardId)
  const step = report.value?.steps.find(item => item.id === stepId)
  return `${agent?.name || shardId} / ${step?.name || (stepId === 'all' ? '全部步骤汇总' : stepId)}`
}
const thresholds = computed(() => report.value?.thresholds || [])
const p95Reference = computed(() => {
  const item = thresholds.value.find(item => item.key === 'p95_ms' && item.operator === 'less_than_or_equal')
  return item && Number.isFinite(Number(item.expected)) && Number(item.expected) >= 0 ? Number(item.expected) : undefined
})
const agents = computed(() => report.value?.agents || report.value?.nodes || [])
const scenarioName = computed(() => {
  const value = selectedRun.value?.configuration.scenario
  return value && typeof value === 'object' && 'name' in value ? String(value.name || '') : ''
})
const applicationOptions = computed(() => context.projects.filter(project => project.id === context.projectId || store.runs.some(run => run.project_id === project.id)))
const matchingRuns = computed(() => {
  const keyword = runQuery.value.trim().toLocaleLowerCase('zh-CN')
  return store.runs.filter(run => (!applicationFilter.value || run.project_id === applicationFilter.value)
    && (runState.value === 'all' || run.state === runState.value)
    && (!keyword || `${runName(run)} ${run.id}`.toLocaleLowerCase('zh-CN').includes(keyword)))
})
const visibleRuns = computed(() => matchingRuns.value.slice(0, historyLimit.value))
const selectedApplicationName = computed(() => selectedRun.value ? applicationName(selectedRun.value.project_id) : '未选择应用')
let liveTimer: ReturnType<typeof setTimeout> | null = null

onMounted(async () => {
  await Promise.all([context.loadSavedContext(), context.loadOptions()])
  if (!context.projectId) return
  await store.loadRuns(undefined)
  const requestedRunId = String(route.query.run_id || '')
  const requestedRun = store.runs.find(item => item.id === requestedRunId)
  applicationFilter.value = requestedRun?.project_id || context.projectId
  runId.value = requestedRunId || matchingRuns.value[0]?.id || ''
  await openRun()
  scheduleLiveRefresh()
})
onBeforeUnmount(() => { store.disconnectRunEvents(); if (liveTimer) clearTimeout(liveTimer) })
watch(runId, async (next, previous) => { if (next !== previous) await openRun() })
watch([runQuery, runState], () => { historyLimit.value = 8 })
watch(applicationFilter, next => {
  historyLimit.value = 8
  if (next && selectedRun.value?.project_id !== next) selectRun(matchingRuns.value[0]?.id || '', false)
})
watch(() => route.query.run_id, next => {
  const requested = String(next || '')
  if (requested && requested !== runId.value && store.runs.some(item => item.id === requested)) runId.value = requested
})
watch(() => selectedRun.value?.state, async state => {
  if (state && ['finished', 'failed', 'cancelled'].includes(state) && !report.value) {
    store.disconnectRunEvents(false)
    await loadReport()
  }
})

async function openRun(): Promise<void> {
  store.disconnectRunEvents()
  store.runEvents = []
  report.value = null
  analysis.value = null
  feedback.value = ''
  if (!runId.value) return
  loading.value = true
  try {
    const run = await store.loadRun(runId.value)
    if (['finished', 'failed', 'cancelled'].includes(run.state)) await loadReport()
    else await store.connectRunEvents(run.id)
  } finally { loading.value = false }
}
function selectRun(nextRunId: string, closeHistory = true): void {
  if (!nextRunId) return
  runId.value = nextRunId
  if (closeHistory) historyOpen.value = false
  if (String(route.query.run_id || '') !== nextRunId) {
    router.replace({ query: { ...route.query, run_id: nextRunId } })
  }
}
function scheduleLiveRefresh(): void {
  if (liveTimer) clearTimeout(liveTimer)
  liveTimer = setTimeout(async () => {
    liveTimer = null
    if (runId.value && selectedRun.value && (!terminal.value || monitoring.value?.terminal === false || (!monitoring.value && selectedRun.value.configuration.monitoring))) {
      await store.loadRun(runId.value).catch(() => undefined)
      if (terminal.value) await loadReport().catch(() => undefined)
    }
    scheduleLiveRefresh()
  }, 3000)
}
async function loadReport(): Promise<void> {
  if (!runId.value) return
  const [nextReport, nextAnalysis] = await Promise.all([store.loadReport(runId.value), store.loadAiAnalysis(runId.value)])
  report.value = nextReport
  analysis.value = nextAnalysis
}
async function reanalyze(): Promise<void> {
  if (!runId.value) return
  analyzing.value = true
  try { analysis.value = await store.requestAiAnalysis(runId.value, true); feedback.value = 'AI重新诊断已排队，不会重新执行压测。' }
  finally { analyzing.value = false }
}
async function notify(): Promise<void> {
  if (!runId.value) return
  feedback.value = await store.notifyReport(runId.value)
}
async function stop(): Promise<void> {
  if (!selectedRun.value) return
  await store.stopRun(selectedRun.value.id)
  feedback.value = '停止请求已提交，正在等待节点保存已完成证据。'
}
function number(section: Record<string, unknown> | undefined, key: string): number { return Number(section?.[key] || 0) }
function percent(value: unknown): string { return value == null || !Number.isFinite(Number(value)) || !hasRequests.value ? '—' : `${(Number(value) * 100).toFixed(2)}%` }
function thresholdText(item: Record<string, unknown>): string { return `${item.operator_label || item.operator} ${thresholdValue(item, item.expected)}` }
function thresholdValue(item: Record<string, unknown>, value: unknown): string {
  if (value == null) return '—'
  const key = String(item.key || '')
  if (key.endsWith('_ms')) return `${value} 毫秒`
  if (key.endsWith('_rate')) return `${(Number(value) * 100).toFixed(2)}%`
  return `${value}${key.endsWith('_per_second') ? ' 次/秒' : ''}`
}
function runName(run: { id: string; configuration: Record<string, unknown> }): string {
  const value = run.configuration.scenario
  return value && typeof value === 'object' && 'name' in value ? String(value.name || run.id) : run.id
}
function stateLabel(value: string): string {
  return ({ draft: '等待检查', preflighting: '预检中', queued: '预检通过', starting: '等待节点就绪', running: '运行中', stopping: '停止中', finished: '已完成', failed: '失败', cancelled: '已取消' } as Record<string, string>)[value] || value
}
function runDate(value: string): string {
  if (!value) return '时间未知'
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString('zh-CN', { hour12: false })
}
function applicationName(projectId: string): string {
  return context.projects.find(item => item.id === projectId)?.name || '历史应用'
}
function loadModelLabel(value: string): string {
  return ({ 'constant-vus': '固定并发', 'ramping-vus': '阶梯并发', 'constant-arrival-rate': '固定吞吐', 'ramping-arrival-rate': '阶梯吞吐' } as Record<string, string>)[value] || value
}
function objectValue(value: unknown): Record<string, unknown> {
  return value && typeof value === 'object' ? value as Record<string, unknown> : {}
}
function allocationText(agent: Record<string, unknown>): string {
  const allocation = objectValue(agent.allocation)
  const parts = []
  if (Number(allocation.vus || 0) > 0) parts.push(`${Number(allocation.vus)} VU`)
  if (Number(allocation.rate || 0) > 0) parts.push(`${Number(allocation.rate)} 次/秒`)
  return parts.join(' · ') || '未分配'
}
function tierLabel(value: unknown): string {
  return ({ preferred: '首选节点', normal: '普通节点', fallback: '备用节点' } as Record<string, string>)[String(value || '')] || String(value || '未标记')
}
function agentTier(agent: Record<string, unknown>): string { return tierLabel(objectValue(agent.allocation).scheduling_tier) }
function agentExitLabel(agent: Record<string, unknown>): string {
  const value = objectValue(agent.summary).exit_code
  return value === 0 ? '正常退出' : `退出码 ${value ?? '未上报'}`
}
function agentBucketCount(agent: Record<string, unknown>): number { return Number(objectValue(agent.summary).metric_bucket_count || 0) }
function agentErrorText(agent: Record<string, unknown>): string {
  const error = objectValue(agent.error)
  return String(error.message || error.code || '节点执行失败').trim()
}
function hasAgentError(agent: Record<string, unknown>): boolean {
  const error = objectValue(agent.error)
  return Boolean(String(error.message || error.code || '').trim())
}
</script>

<template>
  <section class="workspace load-readable-report" data-testid="load-reports-page">
    <header class="page-toolbar load-page-toolbar"><div><p class="eyebrow">性能测试</p><h1>性能报告</h1><p class="page-subtitle">先看是否达标，再看响应速度、失败原因和改进建议。</p></div><div class="load-toolbar-actions"><button class="secondary-command" type="button" :disabled="!runId" @click="openRun"><RefreshCw :size="15" />刷新</button><button v-if="canNotify && report" data-testid="load-notify" class="secondary-command" type="button" @click="notify"><Bell :size="15" />发送飞书报告</button></div></header>
    <section class="load-report-switcher" aria-label="选择压测执行">
      <button data-testid="load-report-history-toggle" class="load-report-switcher-trigger" type="button" :aria-expanded="historyOpen" @click="historyOpen = !historyOpen"><span><History :size="16" /><b>历史执行</b><small>{{ selectedApplicationName }} · {{ scenarioName || '选择一次执行' }}</small></span><span>{{ matchingRuns.length }} 条<ChevronDown :size="15" :class="{ rotated: historyOpen }" /></span></button>
      <div v-if="historyOpen" class="load-report-browser" data-testid="load-report-history-list">
        <div class="load-report-filters"><input v-model="runQuery" type="search" placeholder="搜索场景或执行编号" /><label><span>应用</span><select v-model="applicationFilter" data-testid="load-report-application"><option v-for="project in applicationOptions" :key="project.id" :value="project.id">{{ project.name }}</option></select></label><label><span>状态</span><select v-model="runState"><option value="all">全部状态</option><option value="running">运行中</option><option value="finished">已完成</option><option value="failed">失败</option><option value="cancelled">已取消</option></select></label></div>
        <div class="load-report-run-list"><button v-for="run in visibleRuns" :key="run.id" :data-testid="`report-run-${run.id}`" :class="{ active: run.id === runId }" type="button" @click="selectRun(run.id)"><span><em>{{ applicationName(run.project_id) }}</em><strong>{{ runName(run) }}</strong><small>{{ runDate(run.created_at) }} · {{ loadModelLabel(run.load_model) }}</small></span><b :class="`state-${run.state}`">{{ stateLabel(run.state) }}</b></button><p v-if="!visibleRuns.length" class="compact-empty">没有匹配的执行记录。</p></div>
        <footer v-if="matchingRuns.length" class="load-report-history-footer"><span>当前显示 {{ visibleRuns.length }} / {{ matchingRuns.length }} 条</span><button v-if="visibleRuns.length < matchingRuns.length" class="secondary-command" type="button" @click="historyLimit += 8">显示更多</button></footer>
      </div>
    </section>
    <p v-if="loading" class="state-message">正在读取执行证据…</p>
    <p v-else-if="!selectedRun" class="management-empty">当前项目还没有压测执行。</p>
    <template v-else>
      <LoadRunConsole v-if="!terminal" :run="selectedRun" :events="store.runEvents" :connection-state="store.runConnectionState" @stop="stop" />
      <LoadResourceMonitoring v-if="!terminal && monitoring" :monitoring="monitoring" />
      <template v-if="report">
        <section data-testid="load-report-decision-hero" :class="['load-decision-hero', `tone-${report.verdict}`]"><i class="load-decision-orbit" aria-hidden="true" /><header><div><span>管理层摘要 · 性能测试结果</span><h2>性能决策简报</h2><p>{{ selectedApplicationName }} / {{ scenarioName || '未命名场景' }} · {{ runDate(selectedRun?.created_at || '') }}</p></div><b><i />{{ report.verdict_label }}</b></header><div class="load-decision-message"><strong>{{ report.verdict === 'passed' ? '本次性能目标已达成' : report.verdict === 'failed' ? '本次未达到设定的性能标准' : '当前证据不足，暂不建议下结论' }}</strong><span>{{ report.verdict_explanation }}</span></div><div class="load-decision-grid"><p><span>目标压力</span><strong>{{ report.load_goal.reached ? '已达到' : '未达到' }}</strong><small>{{ report.load_goal.reached ? '仍需结合采样完整性与性能标准' : '结果不代表目标容量' }}</small></p><p><span>性能标准</span><strong>{{ thresholds.filter(item => item.passed).length }}/{{ thresholds.length }}</strong><small>阈值通过 · P95 {{ latency('p95_ms') }} 毫秒</small></p><p><span>执行数据完整度</span><strong>{{ report.evidence.finished_shards }}/{{ report.evidence.total_shards }}</strong><small>{{ evidenceDescription }}</small></p></div></section>
        <section v-if="terminal && sampleIntegrity?.consistent === false" :class="sampleIntegrity?.acceptable ? 'state-message' : 'state-message state-error'" data-testid="load-report-sample-integrity" aria-label="采样完整性检查">
          <strong>{{ sampleIntegrity.acceptable ? '少量计数偏差，允许继续判定，耗时指标仅基于已收到的样本' : '采样计数不一致，当前报告不能用于性能达标判断' }}</strong>
          <p>同一节点、同一步骤的请求数与耗时样本数不一致。以下数据需要核对，不能将缺少的样本视为成功或零耗时。</p>
          <p v-if="sampleIntegrity.tolerance">容差：每个节点和步骤最多相差 {{ sampleIntegrity.tolerance.max_count }} 条，且不超过较大计数的 {{ sampleIntegrity.tolerance.max_ratio * 100 }}%。原始数量不作修补。</p><details><summary>查看计数差异（{{ sampleIntegrity.mismatches.length }} 项）</summary><ul><li v-for="item in sampleIntegrity.mismatches" :key="`${item.shard_id}:${item.step_id}`">{{ integrityLocation(item.shard_id, item.step_id) }}：请求 {{ item.requests }} 次 / 耗时样本 {{ item.latency_samples }} 条</li></ul></details>
        </section>
        <section class="load-statistical-basis"><h2>实际压力与统计口径</h2><p>{{ report.load_goal.explanation || '此历史报告未记录负载判定说明。' }}</p><p v-if="report.load_goal.vu_evidence">实际并发达标持续时间：{{ objectValue(report.load_goal.vu_evidence).sustained_seconds ?? '—' }} 秒；没有采样不能用配置值代替。</p><p v-if="report.statistical_basis">吞吐分母：{{ objectValue(report.statistical_basis).rate_duration_seconds }} 秒（{{ objectValue(report.statistical_basis).rate_duration_basis }}）。调度及收尾在内的观测时间：{{ objectValue(report.statistical_basis).observed_wall_seconds }} 秒。</p><p v-if="report.load_goal.requires_stage_evidence" class="state-message state-error">阶梯各阶段尚缺实际发起量的对齐证据，本次不作负载达标结论。</p></section>
        <section class="load-metric-grid"><article><span>每秒请求数（吞吐量）</span><strong>{{ number(report.transport, 'requests_per_second') }}</strong><small>次/秒 · 总请求 {{ number(report.transport, 'requests') }}</small></article><article><span>HTTP 错误率</span><strong>{{ percent(report.transport.http_error_rate) }}</strong><small>请求超时、连接失败或 HTTP 状态异常</small></article><article><span>业务失败率</span><strong>{{ report.business?.assertions === 0 ? '—' : percent(report.business?.failure_rate) }}</strong><small>{{ report.business?.assertions === 0 ? '未采集业务断言，不能判断' : '接口返回结果不符合业务预期' }}</small></article><article><span>完整链路失败率</span><strong>{{ report.workflow?.iterations === 0 ? '—' : percent(report.workflow?.failure_rate) }}</strong><small>{{ report.workflow?.iterations === 0 ? '未采集完整链路，不能判断' : '业务链路中任一步失败，即记为失败' }}</small></article></section>
        <section class="load-latency"><h2>响应速度 · 多数请求有多快？</h2><p class="report-explainer">P95 不是通过率：例如 P95 = 200 毫秒，表示约 95% 的请求在 200 毫秒内完成。耗时越低越好。</p><div><span v-for="item in percentileLabels" :key="item.key">{{ item.name }}<strong>{{ latency(item.key) }} <small>毫秒</small></strong><small>{{ item.description }}</small></span></div><p v-if="!hasRequests" class="report-explainer">本次没有请求样本，“—”表示无法计算，不代表零耗时或零错误。</p></section>
        <section class="load-thresholds"><header><div><h2>性能阈值</h2><p>先确认目标负载已达到，再检查以下标准；全部通过也只代表本次场景和压力条件。</p></div></header><div><article v-for="item in thresholds" :key="String(item.key)" :class="{ failed: !item.passed }"><strong>{{ item.label }}</strong><span>要求 {{ thresholdText(item) }}</span><span>实际 {{ thresholdValue(item, item.actual) }}</span><b>{{ item.status_label || (item.passed ? '通过' : '未通过') }}</b></article><p v-if="!thresholds.length" class="compact-empty">本次未配置性能阈值。</p></div></section>
        <LoadResourceMonitoring :monitoring="monitoring" />
        <LoadMetricChart :series="report.series || []" :reference-p95="p95Reference" :missing-windows="number(report.evidence, 'missing_windows')" />
        <section class="load-step-statistics"><h2>接口与步骤统计</h2><p class="report-explainer">按 P95 耗时从高到低排列，优先排查慢请求。错误率用于区分请求异常和业务结果异常。</p><div class="report-table-scroll"><table><thead><tr><th>接口 / 步骤</th><th>请求数</th><th>P95（毫秒）</th><th>HTTP 错误率</th><th>业务失败率</th></tr></thead><tbody><tr v-for="step in report.steps || []" :key="String(step.id)"><th>{{ step.name || step.id }}</th><td>{{ step.requests }}</td><td>{{ Number(step.requests) > 0 ? step.p95_ms : '—' }}</td><td>{{ Number(step.requests) > 0 ? percent(step.http_error_rate) : '—' }}</td><td>{{ Number(step.requests) > 0 ? percent(step.business_failure_rate) : '—' }}</td></tr></tbody></table></div><p v-if="!report.steps?.length" class="compact-empty">本次没有接口明细，无法定位到具体慢接口。</p></section>
        <section class="load-agent-report"><h2>节点明细</h2><details v-for="agent in agents" :key="String(agent.id)"><summary>{{ agent.name || agent.id }} · {{ agent.state_label || agent.state }}</summary><dl class="load-agent-facts"><dt>分配压力</dt><dd>{{ allocationText(agent) }}</dd><dt>调度级别</dt><dd>{{ agentTier(agent) }}</dd><dt>进程结果</dt><dd>{{ agentExitLabel(agent) }}</dd><dt>指标窗口</dt><dd>{{ agentBucketCount(agent) }} 个</dd></dl><p v-if="hasAgentError(agent)" class="state-message state-error">{{ agentErrorText(agent) }}</p><details class="load-agent-technical"><summary>查看技术明细（JSON）</summary><pre>{{ JSON.stringify(agent, null, 2) }}</pre></details></details><p v-if="!agents.length" class="compact-empty">没有节点证据。</p></section>
        <p v-if="report.comparison?.compatible === false" class="load-warning">历史运行不可直接对比：{{ report.comparison.reason }}</p>
        <LoadAiAnalysis :analysis="analysis" :loading="analyzing" @reanalyze="reanalyze" />
      </template>
      <p v-else-if="terminal" class="state-message state-error">报告尚未生成，请刷新；确定性报告缺失时不能只看 AI 诊断。</p>
    </template>
    <p v-if="feedback" class="load-feedback">{{ feedback }}</p>
  </section>
</template>

<style scoped>
.load-statistical-basis { padding: 16px 20px; margin: 16px 0; border-left: 3px solid #237ca6; background: #edf6fc; border-radius: 6px; }.load-statistical-basis p { font-size: 13px; line-height: 1.7; color: #435970; }.load-readable-report { max-width: 1560px; margin-inline: auto; line-height: 1.6; color: #223249; }
.load-readable-report h1 { font-size: 28px; letter-spacing: -.5px; }
.load-readable-report h2, .load-readable-report :deep(.load-chart h3) { font-size: 18px; font-weight: 700; line-height: 1.4; }
.load-readable-report .page-subtitle, .report-explainer { font-size: 13px; color: #52647b; line-height: 1.7; margin: 6px 0 16px; }
.load-readable-report .load-decision-hero { padding: 24px; border-radius: 12px; }
.load-readable-report .load-decision-hero header h2 { font-size: 26px; }
.load-readable-report .load-decision-hero header p, .load-readable-report .load-decision-hero header span, .load-readable-report .load-decision-message span { font-size: 13px; line-height: 1.7; }
.load-readable-report .load-decision-hero header h2 { color: #f5fbff; }.load-readable-report .load-decision-message strong { font-size: 22px; color: #f5fbff; }.load-readable-report .tone-failed .load-decision-message strong { color: #ffcf91; }
.load-readable-report .load-decision-grid p > span, .load-readable-report .load-decision-grid small { font-size: 12px; }
.load-readable-report .load-metric-grid { gap: 12px; border: 0; background: transparent; margin: 18px 0; }
.load-readable-report .load-metric-grid article { border: 1px solid #dae3ed; border-radius: 9px; padding: 18px; background: white; }
.load-readable-report .load-metric-grid article > span { font-size: 13px; font-weight: 600; color: #465a74; }
.load-readable-report .load-metric-grid strong { font-size: 30px; line-height: 1.5; font-variant-numeric: tabular-nums; }
.load-readable-report .load-metric-grid small { font-size: 12px; line-height: 1.6; }
.load-readable-report .load-latency, .load-readable-report .load-thresholds, .load-readable-report :deep(.load-chart), .load-step-statistics, .load-readable-report .load-agent-report, .load-readable-report :deep(.load-ai-panel) { padding: 20px; border: 1px solid #dae3ed; border-radius: 10px; background: white; margin-bottom: 18px; }
.load-readable-report .load-latency > div { display: grid; grid-template-columns: repeat(5, minmax(0, 1fr)); }
.load-readable-report .load-latency span { font-size: 13px; padding: 12px; }
.load-readable-report .load-latency strong { font-size: 24px; font-variant-numeric: tabular-nums; }
.load-readable-report .load-latency small { font-size: 12px; font-weight: 400; color: #52647b; }
.load-readable-report .load-thresholds header p { font-size: 13px; line-height: 1.7; }
.load-readable-report .load-thresholds article { padding: 12px; font-size: 13px; align-items: center; }
.report-table-scroll { overflow: auto; } .report-table-scroll table { width: 100%; border-collapse: collapse; font-size: 13px; text-align: right; font-variant-numeric: tabular-nums; }
.report-table-scroll th, .report-table-scroll td { padding: 12px; border-bottom: 1px solid #e2e9f1; white-space: nowrap; }
.report-table-scroll th:first-child { text-align: left; white-space: normal; min-width: 160px; }.report-table-scroll thead { background: #f1f6fb; color: #4a5f78; }
.load-readable-report .load-agent-report summary, .load-readable-report .load-agent-facts dt, .load-readable-report .load-agent-facts dd, .load-readable-report :deep(.load-ai-panel p), .load-readable-report :deep(.load-recommendations span) { font-size: 13px; line-height: 1.7; }
@media(max-width: 1000px) { .load-readable-report .load-latency > div { grid-template-columns: repeat(3, minmax(0, 1fr)); } }
@media(max-width: 620px) { .load-readable-report .load-latency > div { grid-template-columns: repeat(2, minmax(0, 1fr)); }.load-readable-report .load-thresholds article { grid-template-columns: 1fr 1fr; }.load-readable-report .load-decision-hero { padding: 16px; }.load-readable-report .load-metric-grid { grid-template-columns: 1fr 1fr; }.load-readable-report .load-metric-grid article { padding: 12px; }.load-readable-report .load-metric-grid strong { font-size: 25px; } }
</style>
