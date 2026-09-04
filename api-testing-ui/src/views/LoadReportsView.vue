<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { Bell, RefreshCw } from 'lucide-vue-next'
import { useRoute } from 'vue-router'
import LoadAiAnalysis from '../components/LoadAiAnalysis.vue'
import LoadMetricChart from '../components/LoadMetricChart.vue'
import LoadRunConsole from '../components/LoadRunConsole.vue'
import type { LoadAiAnalysis as Analysis, LoadReport } from '../api/contracts'
import { useContextStore } from '../stores/context'
import { useLoadTestingStore } from '../stores/loadTesting'
import { apiTestingHasPermission } from '../utils/authRedirect'

const route = useRoute()
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
const canNotify = apiTestingHasPermission('platform.notify')
const selectedRun = computed(() => store.runs.find(item => item.id === runId.value) || null)
const terminal = computed(() => selectedRun.value ? ['finished', 'failed', 'cancelled'].includes(selectedRun.value.state) : false)
const thresholds = computed(() => report.value?.thresholds || [])
const agents = computed(() => report.value?.agents || report.value?.nodes || [])
const scenarioName = computed(() => {
  const value = selectedRun.value?.configuration.scenario
  return value && typeof value === 'object' && 'name' in value ? String(value.name || '') : ''
})
const visibleRuns = computed(() => {
  const keyword = runQuery.value.trim().toLocaleLowerCase('zh-CN')
  return store.runs.filter(run => (runState.value === 'all' || run.state === runState.value)
    && (!keyword || `${runName(run)} ${run.id}`.toLocaleLowerCase('zh-CN').includes(keyword)))
})
let liveTimer: ReturnType<typeof setTimeout> | null = null

onMounted(async () => {
  await context.loadSavedContext()
  if (!context.projectId) return
  await store.loadRuns(context.projectId)
  runId.value = String(route.query.run_id || store.runs[0]?.id || '')
  await openRun()
  scheduleLiveRefresh()
})
onBeforeUnmount(() => { store.disconnectRunEvents(); if (liveTimer) clearTimeout(liveTimer) })
watch(runId, async (next, previous) => { if (next && next !== previous) await openRun() })
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
function scheduleLiveRefresh(): void {
  if (liveTimer) clearTimeout(liveTimer)
  liveTimer = setTimeout(async () => {
    liveTimer = null
    if (runId.value && selectedRun.value && !terminal.value) await store.loadRun(runId.value).catch(() => undefined)
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
function percent(value: unknown): string { return `${(Number(value || 0) * 100).toFixed(2)}%` }
function thresholdText(item: Record<string, unknown>): string { return `${item.operator_label || item.operator} ${item.expected}` }
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
  <section class="workspace" data-testid="load-reports-page">
    <header class="page-toolbar load-page-toolbar"><div><p class="eyebrow">性能测试</p><h1>性能报告</h1><p class="page-subtitle">先看确定性指标和证据完整性，再参考 AI 诊断。</p></div><div class="load-toolbar-actions"><button class="secondary-command" type="button" :disabled="!runId" @click="openRun"><RefreshCw :size="15" />刷新</button><button v-if="canNotify && report" data-testid="load-notify" class="secondary-command" type="button" @click="notify"><Bell :size="15" />发送飞书报告</button></div></header>
    <section class="load-report-browser" aria-label="选择压测执行">
      <header><div><strong>历史执行</strong><small>按场景、状态和时间选择报告，运行中的任务会自动刷新。</small></div><div class="load-report-filters"><input v-model="runQuery" type="search" placeholder="搜索场景或执行编号" /><select v-model="runState"><option value="all">全部状态</option><option value="running">运行中</option><option value="finished">已完成</option><option value="failed">失败</option><option value="cancelled">已取消</option></select></div></header>
      <div class="load-report-run-list"><button v-for="run in visibleRuns" :key="run.id" :data-testid="`report-run-${run.id}`" :class="{ active: run.id === runId }" type="button" @click="runId = run.id"><span><strong>{{ runName(run) }}</strong><small>{{ runDate(run.created_at) }} · {{ run.load_model }}</small></span><b :class="`state-${run.state}`">{{ stateLabel(run.state) }}</b></button><p v-if="!visibleRuns.length" class="compact-empty">没有匹配的执行记录。</p></div>
    </section>
    <p v-if="loading" class="state-message">正在读取执行证据…</p>
    <p v-else-if="!selectedRun" class="management-empty">当前项目还没有压测执行。</p>
    <template v-else>
      <LoadRunConsole v-if="!terminal" :run="selectedRun" :events="store.runEvents" :connection-state="store.runConnectionState" @stop="stop" />
      <template v-if="report">
        <section class="load-executive-summary"><header><div><span>管理层摘要</span><h2>{{ report.verdict_label }}</h2></div><b :class="`tone-${report.verdict}`">{{ report.verdict === 'passed' ? '达到本次性能目标' : report.verdict === 'failed' ? '存在未达标指标' : '证据不足，不能下结论' }}</b></header><div><p><strong>负载</strong>{{ report.load_goal.reached ? '已达到计划压力' : '未达到计划压力，结果不能代表目标容量' }}</p><p><strong>质量</strong>{{ thresholds.filter(item => item.passed).length }}/{{ thresholds.length }} 项阈值通过，P95 {{ number(report.latency, 'p95_ms') }} ms</p><p><strong>证据</strong>{{ report.evidence.complete ? '全部节点证据完整' : `存在 ${number(report.evidence, 'missing_windows')} 个缺失窗口` }}</p></div></section>
        <section class="load-verdict" :class="`tone-${report.verdict}`"><div><span>确定性结论</span><strong>{{ report.verdict_label }}</strong><p>{{ report.verdict_explanation }}</p></div><dl><dt>场景</dt><dd>{{ scenarioName || '未命名场景' }}</dd><dt>负载目标</dt><dd>{{ report.load_goal.reached ? '已达到' : '未达到' }}</dd><dt>证据完整性</dt><dd>{{ report.evidence.complete ? '完整' : '不完整' }}（节点 {{ report.evidence.finished_shards }}/{{ report.evidence.total_shards }}）</dd></dl></section>
        <section class="load-metric-grid"><article><span>请求吞吐</span><strong>{{ number(report.transport, 'requests_per_second') }}</strong><small>次/秒 · 总请求 {{ number(report.transport, 'requests') }}</small></article><article><span>HTTP 错误率</span><strong>{{ percent(report.transport.http_error_rate) }}</strong><small>网络和 HTTP 层</small></article><article><span>业务失败率</span><strong>{{ percent(report.business?.failure_rate) }}</strong><small>业务断言单独统计</small></article><article><span>完整链路失败率</span><strong>{{ percent(report.workflow?.failure_rate) }}</strong><small>任一步失败即链路失败</small></article></section>
        <section class="load-latency"><h2>响应时间分布</h2><div><span>P50<strong>{{ number(report.latency, 'p50_ms') }} ms</strong></span><span>P90<strong>{{ number(report.latency, 'p90_ms') }} ms</strong></span><span>P95<strong>{{ number(report.latency, 'p95_ms') }} ms</strong></span><span>P99<strong>{{ number(report.latency, 'p99_ms') }} ms</strong></span><span>最大<strong>{{ number(report.latency, 'max_ms') }} ms</strong></span></div></section>
        <section class="load-thresholds"><header><div><h2>性能阈值</h2><p>达到负载目标和阈值通过是两项独立结论。</p></div></header><div><article v-for="item in thresholds" :key="String(item.key)" :class="{ failed: !item.passed }"><strong>{{ item.label }}</strong><span>要求 {{ thresholdText(item) }}</span><span>实际 {{ item.actual }}</span><b>{{ item.passed ? '通过' : '未通过' }}</b></article><p v-if="!thresholds.length" class="compact-empty">本次未配置性能阈值。</p></div></section>
        <LoadMetricChart :series="report.series || []" :missing-windows="number(report.evidence, 'missing_windows')" />
        <section class="load-agent-report"><h2>节点明细</h2><details v-for="agent in agents" :key="String(agent.id)"><summary>{{ agent.name || agent.id }} · {{ agent.state_label || agent.state }}</summary><dl class="load-agent-facts"><dt>分配压力</dt><dd>{{ allocationText(agent) }}</dd><dt>调度级别</dt><dd>{{ agentTier(agent) }}</dd><dt>进程结果</dt><dd>{{ agentExitLabel(agent) }}</dd><dt>指标窗口</dt><dd>{{ agentBucketCount(agent) }} 个</dd></dl><p v-if="hasAgentError(agent)" class="state-message state-error">{{ agentErrorText(agent) }}</p><details class="load-agent-technical"><summary>查看技术明细（JSON）</summary><pre>{{ JSON.stringify(agent, null, 2) }}</pre></details></details><p v-if="!agents.length" class="compact-empty">没有节点证据。</p></section>
        <p v-if="report.comparison?.compatible === false" class="load-warning">历史运行不可直接对比：{{ report.comparison.reason }}</p>
        <LoadAiAnalysis :analysis="analysis" :loading="analyzing" @reanalyze="reanalyze" />
      </template>
      <p v-else-if="terminal" class="state-message state-error">报告尚未生成，请刷新；确定性报告缺失时不能只看 AI 诊断。</p>
    </template>
    <p v-if="feedback" class="load-feedback">{{ feedback }}</p>
  </section>
</template>
