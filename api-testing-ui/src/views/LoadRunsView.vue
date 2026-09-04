<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { Activity, ChartLine, Plus, RefreshCw, RotateCcw, Trash2 } from 'lucide-vue-next'
import { useRouter } from 'vue-router'
import type { LoadAgent, LoadRun } from '../api/contracts'
import LoadRunWizard from '../components/LoadRunWizard.vue'
import { useContextStore } from '../stores/context'
import { useLoadTestingStore } from '../stores/loadTesting'
import { apiTestingHasPermission } from '../utils/authRedirect'

const context = useContextStore()
const store = useLoadTestingStore()
const router = useRouter()
const creating = ref(false)
const selectedScenarioId = ref('')
const feedback = ref('')
const busyId = ref('')
const runMessages = ref<Record<string, string>>({})
const query = ref('')
const stateFilter = ref('all')
const canExecute = apiTestingHasPermission('api.loadtest.execute')
let refreshTimer: ReturnType<typeof setTimeout> | null = null
const selectedScenario = computed(() => store.scenarios.find(item => item.id === selectedScenarioId.value) || store.scenarios.find(item => item.active_version_id) || null)
const projectName = computed(() => context.projects.find(item => item.id === context.projectId)?.name || '当前接口项目')
const filteredRuns = computed(() => {
  const keyword = query.value.trim().toLocaleLowerCase('zh-CN')
  return store.runs.filter(run => (stateFilter.value === 'all' || run.state === stateFilter.value)
    && (!keyword || `${scenarioName(run)} ${run.id} ${environmentName(run)}`.toLocaleLowerCase('zh-CN').includes(keyword)))
})

onMounted(async () => {
  await Promise.all([context.loadSavedContext(), context.loadOptions()])
  await refresh()
  scheduleRefresh()
})
onBeforeUnmount(() => { if (refreshTimer) clearTimeout(refreshTimer) })
function scheduleRefresh(): void {
  if (refreshTimer) clearTimeout(refreshTimer)
  refreshTimer = setTimeout(async () => {
    refreshTimer = null
    if (context.projectId) await store.loadRuns(context.projectId, true)
    scheduleRefresh()
  }, 3000)
}
async function refresh(): Promise<void> {
  if (!context.projectId) return
  await Promise.all([store.loadScenarios(context.projectId), store.loadRuns(context.projectId), store.loadAgents()])
  if (!selectedScenarioId.value) selectedScenarioId.value = store.scenarios.find(item => item.active_version_id)?.id || ''
}
async function create(payload: Record<string, unknown>): Promise<void> {
  try {
    await store.createRun(payload)
    creating.value = false
    feedback.value = '压测草稿已创建。下一步：检查所有所选节点到目标环境的连通性。'
  } catch { /* store exposes the server message */ }
}
function runAgentIds(run: LoadRun): string[] {
  const rows = Array.isArray(run.configuration.agents) ? run.configuration.agents : []
  return rows.flatMap(item => item && typeof item === 'object' && 'id' in item ? [String(item.id)] : [])
}
function connectivityResult(run: LoadRun, agent: LoadAgent): Record<string, unknown> | null {
  const rows = agent.health.target_connectivity
  if (!rows || typeof rows !== 'object' || Array.isArray(rows)) return null
  const result = (rows as Record<string, unknown>)[run.environment_revision_id]
  return result && typeof result === 'object' && !Array.isArray(result) ? result as Record<string, unknown> : null
}
function runAgents(run: LoadRun): LoadAgent[] {
  const ids = new Set(runAgentIds(run))
  return store.agents.filter(agent => ids.has(agent.id))
}
function connectivityReady(run: LoadRun): boolean {
  const agents = runAgents(run)
  return agents.length > 0 && agents.length === runAgentIds(run).length
    && agents.every(agent => connectivityResult(run, agent)?.reachable === true)
}
function connectivityLabel(run: LoadRun, agent: LoadAgent): string {
  const result = connectivityResult(run, agent)
  if (!result) return '等待回传'
  return result.reachable === true ? '连通性通过' : `检查失败：${String(result.message || '目标不可达')}`
}
async function pollConnectivity(run: LoadRun): Promise<boolean> {
  for (let attempt = 0; attempt < 6; attempt += 1) {
    await store.loadAgents()
    if (connectivityReady(run)) return true
    if (attempt < 5) await new Promise(resolve => globalThis.setTimeout(resolve, 1500))
  }
  return false
}
async function action(run: LoadRun, name: 'connectivity' | 'preflight' | 'start' | 'stop'): Promise<void> {
  busyId.value = run.id
  feedback.value = ''
  runMessages.value[run.id] = ({
    connectivity: '正在通知所选节点检查目标环境，请稍候…',
    preflight: '正在执行单用户预检并校验业务断言，请稍候…',
    start: '正在提交启动指令，请稍候…',
    stop: '正在通知所有节点停止并保存证据，请稍候…',
  } as const)[name]
  try {
    if (name === 'connectivity') {
      await store.prepareConnectivity(run.id)
      const ready = await pollConnectivity(run)
      runMessages.value[run.id] = ready ? '✅ 所有节点的目标连通性检查已通过，可以运行单用户预检。' : '连通性检查仍在等待节点回传；未全部通过前不能运行预检。'
    } else if (name === 'preflight') {
      const next = await store.preflightRun(run.id)
      const preflight = next.summary.preflight as { passed?: boolean; message?: string } | undefined
      runMessages.value[run.id] = next.state === 'queued' && preflight?.passed !== false
        ? `✅ ${String(preflight?.message || '单用户预检通过，可以开始压测。')}`
        : `预检未通过：${String(preflight?.message || '请查看失败证据，修正后重新创建压测。')}`
    } else if (name === 'start') {
      await store.startRun(run.id)
      runMessages.value[run.id] = '启动指令已提交，执行状态和节点进度每 3 秒自动刷新。'
    } else {
      await store.stopRun(run.id)
      runMessages.value[run.id] = '停止请求已提交，等待节点保存最后一批证据。'
    }
    feedback.value = runMessages.value[run.id]
  } catch {
    runMessages.value[run.id] = store.runError || '操作失败，请查看页面上方的错误说明。'
    feedback.value = runMessages.value[run.id]
  }
  finally { busyId.value = '' }
}
function rerun(run: LoadRun): void {
  const snapshot = run.configuration.scenario as Record<string, unknown> | undefined
  const scenario = store.scenarios.find(item => item.id === String(snapshot?.id || ''))
    || store.scenarios.find(item => item.active_version_id === run.scenario_version_id)
  if (!scenario) {
    feedback.value = '原场景已归档或不在当前应用中，请到“性能场景”恢复或重新创建后再执行。'
    return
  }
  selectedScenarioId.value = scenario.id
  creating.value = true
  feedback.value = `已选择“${scenario.name}”的当前版本，请核对目标环境和负载后创建新执行。`
}
async function remove(run: LoadRun): Promise<void> {
  if (!window.confirm(`删除“${scenarioName(run)}”的这次执行记录？关联的性能报告和节点证据也会删除。`)) return
  busyId.value = run.id
  try { await store.deleteRun(run.id); feedback.value = '执行记录已删除。' }
  catch (error) { feedback.value = error instanceof Error ? error.message : '执行记录删除失败' }
  finally { busyId.value = '' }
}
function stateLabel(state: string): string {
  return ({ draft: '等待检查', preflighting: '预检中', queued: '预检通过', starting: '等待节点就绪', running: '运行中', stopping: '停止中', finished: '已完成', failed: '失败', cancelled: '已取消' } as Record<string, string>)[state] || state
}
function scenarioName(run: LoadRun): string {
  const value = run.configuration.scenario
  return value && typeof value === 'object' && 'name' in value ? String(value.name || run.id) : run.id
}
function environmentName(run: LoadRun): string {
  const item = context.environmentRevisions.find(row => row.id === run.environment_revision_id)
  return item ? `${item.name} · v${item.revision}` : '历史环境版本'
}
function applicationName(run: LoadRun): string {
  return context.projects.find(item => item.id === run.project_id)?.name || projectName.value
}
function workloadText(run: LoadRun): string {
  const workload = run.configuration.workload as Record<string, unknown> | undefined
  const label = ({
    'constant-vus': '固定并发',
    'ramping-vus': '阶梯并发',
    'constant-arrival-rate': '固定吞吐',
    'ramping-arrival-rate': '阶梯吞吐',
  } as Record<string, string>)[run.load_model] || run.load_model
  if (!workload) return label
  if (run.load_model.includes('arrival-rate')) return `${label} · ${Number(workload.rate || workload.start_rate || 0)} 次/秒 · ${Number(workload.duration_seconds || 0)} 秒`
  return `${label} · ${Number(workload.vus || workload.start_vus || 0)} VU · ${Number(workload.duration_seconds || 0)} 秒`
}
function dateTime(value: string | null): string {
  if (!value) return '暂无'
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString('zh-CN', { hour12: false })
}
const runStages = [
  { key: 'draft', label: '创建草稿' },
  { key: 'connectivity', label: '目标连通性' },
  { key: 'preflight', label: '单用户预检' },
  { key: 'execution', label: '正式执行' },
] as const
function stageState(run: LoadRun, key: typeof runStages[number]['key']): 'done' | 'active' | 'failed' | 'cancelled' | 'pending' {
  if (key === 'draft') return 'done'
  if (key === 'connectivity') {
    if (run.state === 'draft') return connectivityReady(run) ? 'done' : busyId.value === run.id ? 'active' : 'pending'
    return 'done'
  }
  if (key === 'preflight') {
    if (run.state === 'draft') return 'pending'
    if (run.state === 'preflighting') return 'active'
    if (['queued', 'starting', 'running', 'stopping', 'finished'].includes(run.state) || run.started_at) return 'done'
    return run.state === 'cancelled' ? 'cancelled' : 'failed'
  }
  if (run.state === 'finished') return 'done'
  if (run.state === 'failed') return 'failed'
  if (run.state === 'cancelled') return 'cancelled'
  if (['queued', 'starting', 'running', 'stopping'].includes(run.state)) return 'active'
  return 'pending'
}
function stageMarker(run: LoadRun, key: typeof runStages[number]['key'], index: number): string {
  const state = stageState(run, key)
  if (state === 'done') return '✓'
  if (state === 'active') return '●'
  if (state === 'failed') return '!'
  if (state === 'cancelled') return '—'
  return String(index + 1)
}
function canDelete(run: LoadRun): boolean {
  return ['draft', 'finished', 'failed', 'cancelled'].includes(run.state)
}
</script>

<template>
  <section class="workspace" data-testid="load-runs-page">
    <header class="page-toolbar load-page-toolbar"><div><p class="eyebrow">性能测试</p><h1>压测执行</h1><p class="page-subtitle">创建草稿后按“目标连通性 → 单用户预检 → 开始压测”执行；列表每 3 秒自动刷新。</p></div><div class="load-toolbar-actions"><span class="load-live-indicator"><i class="load-status-dot online pulse" />自动刷新</span><button class="secondary-command" type="button" @click="refresh"><RefreshCw :size="15" />立即刷新</button><button v-if="canExecute" data-testid="load-run-new" class="primary-command" type="button" @click="creating = true"><Plus :size="15" />新建压测</button></div></header>
    <p v-if="store.runError" role="alert" class="state-message state-error">{{ store.runError }}</p>
    <template v-if="creating">
      <label class="load-scenario-selector">第 1 步：选择性能场景<select v-model="selectedScenarioId"><option v-for="item in store.scenarios.filter(row => row.active_version_id)" :key="item.id" :value="item.id">{{ item.name }}</option></select></label>
      <LoadRunWizard v-if="selectedScenario" :scenario="selectedScenario" :project-name="projectName" :initial-environment-id="context.environmentRevisionId || undefined" :environments="context.environmentRevisions.filter(item => item.project_id === context.projectId)" :agents="store.agents" @submit="create" @cancel="creating = false" />
      <p v-else class="load-warning">没有可执行场景，请先到“性能场景”保存通过校验的版本。</p>
    </template>
    <template v-else>
      <div class="load-list-toolbar"><label class="search-box"><span class="sr-only">搜索压测执行</span><input v-model="query" type="search" placeholder="搜索场景、执行编号或环境" /></label><label>状态<select v-model="stateFilter"><option value="all">全部状态</option><option value="running">运行中</option><option value="draft">等待检查</option><option value="finished">已完成</option><option value="failed">失败</option><option value="cancelled">已取消</option></select></label></div>
      <div v-if="!store.runs.length" class="management-empty"><Activity :size="28" /><h2>还没有压测执行</h2><p>先准备已校准节点和性能场景，再创建压测草稿。</p></div>
      <p v-else-if="!filteredRuns.length" class="section-empty">没有匹配的执行记录。</p>
      <div v-else class="load-run-list">
        <article v-for="run in filteredRuns" :key="run.id" :class="`run-${run.state}`">
          <header><div><span class="load-project-tag">{{ applicationName(run) }}</span><strong>{{ scenarioName(run) }}</strong><small>{{ environmentName(run) }} · {{ workloadText(run) }} · 创建于 {{ dateTime(run.created_at) }}</small></div><span :class="['load-status-chip', run.state]">{{ stateLabel(run.state) }}</span></header>
          <div class="load-run-progress"><span v-for="(stage, index) in runStages" :key="stage.key" :data-testid="`run-stage-${run.id}-${stage.key}`" :class="stageState(run, stage.key)"><b>{{ stageMarker(run, stage.key, index) }}</b>{{ stage.label }}</span></div>
          <div v-if="run.state === 'draft'" class="load-connectivity-list"><p v-for="agent in runAgents(run)" :key="agent.id" :data-testid="`connectivity-${run.id}-${agent.id}`"><strong>{{ agent.name }}</strong><span>{{ connectivityLabel(run, agent) }}</span></p><p v-if="!runAgents(run).length">节点信息尚未加载，请点击立即刷新。</p></div>
          <p v-if="runMessages[run.id]" :class="['load-run-message', { busy: busyId === run.id }]" aria-live="polite">{{ runMessages[run.id] }}</p>
          <div class="load-run-actions"><button v-if="run.state === 'draft' && !connectivityReady(run)" :data-testid="`run-connectivity-${run.id}`" class="primary-command" type="button" :disabled="busyId === run.id" @click="action(run, 'connectivity')">{{ busyId === run.id ? '正在检查…' : '检查目标连通性' }}</button><button v-if="run.state === 'draft' && connectivityReady(run)" :data-testid="`run-preflight-${run.id}`" class="primary-command" type="button" :disabled="busyId === run.id" @click="action(run, 'preflight')">{{ busyId === run.id ? '正在预检…' : '运行单用户预检' }}</button><button v-if="run.state === 'queued'" :data-testid="`run-start-${run.id}`" class="primary-command" type="button" :disabled="busyId === run.id" @click="action(run, 'start')">开始压测</button><button v-if="['preflighting','starting','running','stopping'].includes(run.state)" :data-testid="`run-stop-${run.id}`" class="danger-command" type="button" :disabled="busyId === run.id" @click="action(run, 'stop')">{{ busyId === run.id ? '正在停止…' : '停止并保存证据' }}</button><button v-if="['finished','failed','cancelled'].includes(run.state) && canExecute" :data-testid="`run-rerun-${run.id}`" class="secondary-command" type="button" @click="rerun(run)"><RotateCcw :size="14" />使用当前场景再次压测</button><button class="secondary-command" type="button" @click="router.push({ name: 'load-reports', query: { run_id: run.id } })"><ChartLine :size="14" />{{ ['starting','running','stopping'].includes(run.state) ? '查看实时报告' : '查看报告' }}</button><button v-if="canExecute && canDelete(run)" :data-testid="`run-delete-${run.id}`" class="text-command danger-text" type="button" :disabled="busyId === run.id" @click="remove(run)"><Trash2 :size="14" />删除</button></div>
        </article>
      </div>
    </template>
    <p v-if="feedback" class="load-feedback">{{ feedback }}</p>
  </section>
</template>
