<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { Activity, ChartLine, Plus, RefreshCw } from 'lucide-vue-next'
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
const canExecute = apiTestingHasPermission('api.loadtest.execute')
const selectedScenario = computed(() => store.scenarios.find(item => item.id === selectedScenarioId.value) || store.scenarios.find(item => item.active_version_id) || null)

onMounted(async () => { await Promise.all([context.loadSavedContext(), context.loadOptions()]); await refresh() })

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
  try {
    if (name === 'connectivity') {
      await store.prepareConnectivity(run.id)
      const ready = await pollConnectivity(run)
      feedback.value = ready ? '所有节点的目标连通性检查已通过，可以运行单用户预检。' : '连通性检查仍在等待节点回传，请稍后刷新；未全部通过前不能运行预检。'
    } else if (name === 'preflight') {
      const next = await store.preflightRun(run.id)
      feedback.value = String((next.summary.preflight as { message?: string } | undefined)?.message || '预检完成')
    } else if (name === 'start') {
      await store.startRun(run.id)
      feedback.value = '启动指令已提交，节点就绪后会统一开始。'
    } else {
      await store.stopRun(run.id)
      feedback.value = '停止请求已提交。'
    }
  } catch { /* store exposes the server message */ }
  finally { busyId.value = '' }
}
function stateLabel(state: string): string {
  return ({ draft: '等待检查', preflighting: '预检中', queued: '预检通过', starting: '等待节点就绪', running: '运行中', stopping: '停止中', finished: '已完成', failed: '失败', cancelled: '已取消' } as Record<string, string>)[state] || state
}
function scenarioName(run: LoadRun): string {
  const value = run.configuration.scenario
  return value && typeof value === 'object' && 'name' in value ? String(value.name || run.id) : run.id
}
</script>

<template>
  <section class="workspace" data-testid="load-runs-page">
    <header class="page-toolbar load-page-toolbar"><div><p class="eyebrow">性能测试</p><h1>压测执行</h1><p class="page-subtitle">创建草稿后按“目标连通性 → 单用户预检 → 开始压测”执行，任何一步失败都会保留证据。</p></div><div class="load-toolbar-actions"><button class="secondary-command" type="button" @click="refresh"><RefreshCw :size="15" />刷新</button><button v-if="canExecute" data-testid="load-run-new" class="primary-command" type="button" @click="creating = true"><Plus :size="15" />新建压测</button></div></header>
    <p v-if="store.runError" role="alert" class="state-message state-error">{{ store.runError }}</p>
    <template v-if="creating"><label class="load-scenario-selector">性能场景<select v-model="selectedScenarioId"><option v-for="item in store.scenarios.filter(row => row.active_version_id)" :key="item.id" :value="item.id">{{ item.name }}</option></select></label><LoadRunWizard v-if="selectedScenario" :scenario="selectedScenario" :environments="context.environmentRevisions.filter(item => item.project_id === context.projectId)" :agents="store.agents" @submit="create" @cancel="creating = false" /><p v-else class="load-warning">没有可执行场景，请先到“性能场景”保存通过校验的版本。</p></template>
    <div v-else-if="!store.runs.length" class="management-empty"><Activity :size="28" /><h2>还没有压测执行</h2><p>先准备已校准节点和性能场景，再创建压测草稿。</p></div>
    <div v-else class="load-run-list">
      <article v-for="run in store.runs" :key="run.id">
        <header><div><strong>{{ scenarioName(run) }}</strong><small>{{ stateLabel(run.state) }} · {{ run.load_model }}</small></div><b>{{ run.verdict === 'inconclusive' ? '证据不足' : run.verdict || '尚未出结论' }}</b></header>
        <div class="load-run-progress"><span>1 创建草稿</span><span>2 目标连通性</span><span>3 单用户预检</span><span>4 正式执行</span></div>
        <div v-if="run.state === 'draft'" class="load-connectivity-list"><p v-for="agent in runAgents(run)" :key="agent.id" :data-testid="`connectivity-${run.id}-${agent.id}`"><strong>{{ agent.name }}</strong><span>{{ connectivityLabel(run, agent) }}</span></p><p v-if="!runAgents(run).length">节点信息尚未加载，请点击刷新。</p></div>
        <div class="load-run-actions"><button :data-testid="`run-connectivity-${run.id}`" class="secondary-command" type="button" :disabled="run.state !== 'draft' || busyId === run.id" @click="action(run, 'connectivity')">检查目标连通性</button><button :data-testid="`run-preflight-${run.id}`" class="secondary-command" type="button" :disabled="run.state !== 'draft' || !connectivityReady(run) || busyId === run.id" :title="!connectivityReady(run) ? '所有节点连通性通过后才能预检' : ''" @click="action(run, 'preflight')">运行预检</button><button :data-testid="`run-start-${run.id}`" class="primary-command" type="button" :disabled="run.state !== 'queued' || busyId === run.id" @click="action(run, 'start')">开始压测</button><button :data-testid="`run-stop-${run.id}`" class="danger-command" type="button" :disabled="['finished','failed','cancelled'].includes(run.state) || busyId === run.id" @click="action(run, 'stop')">停止</button><button class="text-command" type="button" @click="router.push({ name: 'load-reports', query: { run_id: run.id } })"><ChartLine :size="14" />查看报告</button></div>
      </article>
    </div>
    <p v-if="feedback" class="load-feedback">{{ feedback }}</p>
  </section>
</template>
