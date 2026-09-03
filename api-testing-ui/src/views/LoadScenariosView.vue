<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { Plus, RefreshCw } from 'lucide-vue-next'
import type { LoadScenarioDefinition } from '../api/contracts'
import LoadScenarioWizard from '../components/LoadScenarioWizard.vue'
import { useAssetsStore } from '../stores/assets'
import { useContextStore } from '../stores/context'
import { useLoadTestingStore } from '../stores/loadTesting'
import { apiTestingHasPermission } from '../utils/authRedirect'

const context = useContextStore(); const assets = useAssetsStore(); const store = useLoadTestingStore()
const creating = ref(false); const saving = ref(false); const localError = ref('')
const canEdit = apiTestingHasPermission('api.loadtest.edit')
onMounted(async () => { await Promise.all([context.loadSavedContext(), context.loadOptions()]); await refresh() })
async function refresh(): Promise<void> { if (!context.projectId) return; await store.loadScenarios(context.projectId); if (context.sourceRevisionId) await assets.load(context.sourceRevisionId) }
async function save(definition: LoadScenarioDefinition): Promise<void> {
  if (!context.projectId) { localError.value = '请先在工作台选择接口项目'; return }
  saving.value = true; localError.value = ''; store.scenarioError = ''
  let scenarioId = ''
  try {
    const scenario = await store.createScenario({ project_id: context.projectId, name: definition.name, description: definition.description, scenario_type: definition.mode })
    scenarioId = scenario.id
    await store.saveScenarioVersion(scenario.id, definition); creating.value = false
  } catch (error) {
    if (scenarioId) await store.archiveScenario(scenarioId).catch(() => undefined)
    localError.value = error instanceof Error ? error.message : '性能场景保存失败'
  }
  finally { saving.value = false }
}
</script>
<template><section class="workspace" data-testid="load-scenarios-page"><header class="page-toolbar load-page-toolbar"><div><p class="eyebrow">性能测试</p><h1>性能场景</h1><p class="page-subtitle">把已同步接口整理成可重复执行的单接口或业务链路，版本保存后不可修改。</p></div><div class="load-toolbar-actions"><button class="secondary-command" type="button" @click="refresh"><RefreshCw :size="15" />刷新</button><button v-if="canEdit" data-testid="load-scenario-new" class="primary-command" type="button" @click="creating = true"><Plus :size="15" />新建场景</button></div></header>
<p v-if="localError || store.scenarioError" role="alert" class="state-message state-error">{{ localError || store.scenarioError }}</p>
<LoadScenarioWizard v-if="creating" :endpoints="assets.endpoints" @save="save" @cancel="creating = false" />
<p v-else-if="store.loadingScenarios" class="state-message">正在读取性能场景…</p><div v-else-if="!store.scenarios.length" class="management-empty"><h2>还没有性能场景</h2><p>从接口资产中选择安全的只读接口，创建第一个可校验场景。</p></div><div v-else class="load-scenario-list"><article v-for="item in store.scenarios" :key="item.id"><div><strong>{{ item.name }}</strong><small>{{ item.scenario_type === 'workflow' ? '业务链路压测' : '单接口压测' }} · {{ item.active_version_id ? '已有可用版本' : '等待保存版本' }}</small></div><p>{{ item.description || '未填写说明' }}</p></article></div>
<p v-if="saving" class="load-feedback">正在由服务端校验并保存不可变版本…</p></section></template>
