<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { Activity, Archive, Pencil, Plus, RefreshCw } from 'lucide-vue-next'
import { useRouter } from 'vue-router'
import type { LoadScenario, LoadScenarioDefinition } from '../api/contracts'
import LoadScenarioWizard from '../components/LoadScenarioWizard.vue'
import { useAssetsStore } from '../stores/assets'
import { useContextStore } from '../stores/context'
import { useLoadTestingStore } from '../stores/loadTesting'
import { apiTestingHasPermission } from '../utils/authRedirect'

const context = useContextStore(); const assets = useAssetsStore(); const store = useLoadTestingStore()
const router = useRouter()
const creating = ref(false); const saving = ref(false); const localError = ref('')
const editingScenario = ref<LoadScenario | null>(null)
const initialDefinition = ref<LoadScenarioDefinition | null>(null)
const canEdit = apiTestingHasPermission('api.loadtest.edit')
const projectName = computed(() => context.projects.find(item => item.id === context.projectId)?.name || '未选择应用')

onMounted(async () => { await Promise.all([context.loadSavedContext(), context.loadOptions()]); await refresh() })
async function refresh(): Promise<void> {
  if (!context.projectId) return
  await store.loadScenarios(context.projectId)
  if (context.sourceRevisionId) await assets.load(context.sourceRevisionId)
}
function openNew(): void {
  editingScenario.value = null
  initialDefinition.value = null
  localError.value = ''
  creating.value = true
}
async function edit(item: LoadScenario): Promise<void> {
  if (!item.active_version_id) { localError.value = '该场景还没有可编辑版本，请先创建版本。'; return }
  localError.value = ''
  try {
    const version = await store.loadScenarioVersion(item.active_version_id)
    if (!version.definition) throw new Error('场景版本缺少定义')
    editingScenario.value = item
    initialDefinition.value = version.definition
    creating.value = true
  } catch (error) {
    localError.value = error instanceof Error ? error.message : '读取场景版本失败'
  }
}
function closeWizard(): void {
  creating.value = false
  editingScenario.value = null
  initialDefinition.value = null
}
function startRun(item: LoadScenario): void {
  router.push({ name: 'load-runs', query: { scenario_id: item.id } })
}
async function save(definition: LoadScenarioDefinition): Promise<void> {
  if (!context.projectId) { localError.value = '请先在工作台选择接口项目'; return }
  saving.value = true; localError.value = ''; store.scenarioError = ''
  let scenarioId = editingScenario.value?.id || ''
  try {
    if (scenarioId) {
      await store.updateScenario(scenarioId, { name: definition.name, description: definition.description })
    } else {
      const scenario = await store.createScenario({ project_id: context.projectId, name: definition.name, description: definition.description, scenario_type: definition.mode })
      scenarioId = scenario.id
    }
    await store.saveScenarioVersion(scenarioId, definition)
    closeWizard()
  } catch (error) {
    if (scenarioId && !editingScenario.value) await store.archiveScenario(scenarioId).catch(() => undefined)
    localError.value = error instanceof Error ? error.message : '性能场景保存失败'
  } finally { saving.value = false }
}
async function archive(item: LoadScenario): Promise<void> {
  if (!window.confirm(`归档“${item.name}”？历史执行和报告仍会保留。`)) return
  try { await store.archiveScenario(item.id) }
  catch (error) { localError.value = error instanceof Error ? error.message : '场景归档失败' }
}
function dateTime(value: string): string {
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? value || '暂无' : date.toLocaleString('zh-CN', { hour12: false })
}
</script>

<template>
  <section class="workspace" data-testid="load-scenarios-page">
    <header class="page-toolbar load-page-toolbar"><div><p class="eyebrow">性能测试</p><h1>性能场景</h1><p class="page-subtitle">按应用组织可重复执行的单接口或业务链路；修改会创建新版本，历史结果不受影响。</p></div><div class="load-toolbar-actions"><button class="secondary-command" type="button" @click="refresh"><RefreshCw :size="15" />刷新</button><button v-if="canEdit" data-testid="load-scenario-new" class="primary-command" type="button" @click="openNew"><Plus :size="15" />新建场景</button></div></header>
    <section class="load-context-banner load-page-context"><div><span>所属应用 / API 项目</span><strong>{{ projectName }}</strong><small>接口来源：当前工作区已选择版本；切换应用或接口版本请回到工作台。</small></div><div><span>当前接口资产</span><strong>{{ assets.endpoints.length }} 个接口</strong><small>创建场景时只能选择当前项目和接口版本中的资产。</small></div></section>
    <p v-if="localError || store.scenarioError" role="alert" class="state-message state-error">{{ localError || store.scenarioError }}</p>
    <LoadScenarioWizard v-if="creating" :endpoints="assets.endpoints" :project-name="projectName" :initial-definition="initialDefinition" @save="save" @cancel="closeWizard" />
    <p v-else-if="store.loadingScenarios" class="state-message">正在读取性能场景…</p>
    <div v-else-if="!store.scenarios.length" class="management-empty"><h2>还没有性能场景</h2><p>先确认上方应用和接口版本，再从安全的只读接口创建第一个场景。</p></div>
    <div v-else class="load-scenario-list">
      <article v-for="item in store.scenarios" :key="item.id">
        <header><div><strong>{{ item.name }}</strong><small>{{ item.scenario_type === 'workflow' ? '业务链路压测' : '单接口压测' }} · {{ item.active_version_id ? '已有可用版本' : '等待保存版本' }}</small></div><span class="load-status-chip ready">可用</span></header>
        <p>{{ item.description || '未填写说明' }}</p>
        <footer><div class="load-card-actions"><button :data-testid="`scenario-run-${item.id}`" class="primary-command" type="button" @click="startRun(item)"><Activity :size="14" />创建压测</button><button v-if="canEdit" :data-testid="`scenario-edit-${item.id}`" class="secondary-command" type="button" @click="edit(item)"><Pencil :size="14" />编辑并创建新版本</button><button v-if="canEdit" :data-testid="`scenario-archive-${item.id}`" class="danger-command" type="button" @click="archive(item)"><Archive :size="14" />归档</button></div><small>更新于 {{ dateTime(item.updated_at) }}</small></footer>
      </article>
    </div>
    <p v-if="saving" class="load-feedback">正在由服务端校验并保存不可变版本…</p>
  </section>
</template>
