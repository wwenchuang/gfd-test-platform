<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { Bug, RefreshCw } from 'lucide-vue-next'

import AiAssistant from '../components/AiAssistant.vue'
import CaseEditor from '../components/CaseEditor.vue'
import ContextBar from '../components/ContextBar.vue'
import DebugDrawer from '../components/DebugDrawer.vue'
import EndpointDetail from '../components/EndpointDetail.vue'
import EndpointTree from '../components/EndpointTree.vue'
import type { ApiEndpoint, CaseDraft } from '../api/contracts'
import { useAssetsStore } from '../stores/assets'
import { useCasesStore } from '../stores/cases'
import { useContextStore } from '../stores/context'

const context = useContextStore()
const assets = useAssetsStore()
const cases = useCasesStore()
const selectedIds = ref<string[]>([])
const activeEndpoint = ref<ApiEndpoint | null>(null)
const debugOpen = ref(false)
const localError = ref('')
const activeDraft = computed(() => activeEndpoint.value ? cases.draftFor(activeEndpoint.value) : null)
const activeVersionId = computed(() => activeEndpoint.value ? cases.versionByEndpoint[activeEndpoint.value.id] || '' : '')
const debugRunning = computed(() => !!cases.debugExecution && !['DONE','CANCELLED','PASSED','FAILED','BROKEN'].includes(cases.debugExecution.state))

onMounted(async () => {
  await context.loadSavedContext()
  if (context.sourceRevisionId) await assets.load(context.sourceRevisionId)
})

function activate(endpoint: ApiEndpoint): void {
  activeEndpoint.value = endpoint
  cases.draftFor(endpoint)
}
function updateDraft(draft: CaseDraft): void {
  if (activeEndpoint.value) cases.updateDraft(activeEndpoint.value.id, draft)
}
async function saveDraft(): Promise<void> {
  if (!activeEndpoint.value) return
  localError.value = ''
  try { await cases.save(activeEndpoint.value.id) } catch (error) { localError.value = error instanceof Error ? error.message : '草稿保存失败' }
}
async function generate(intent: string): Promise<void> {
  if (!context.environmentRevisionId) { localError.value = '请先选择执行环境'; return }
  await cases.generate(selectedIds.value, context.environmentRevisionId, intent)
  const firstGenerated = cases.aiJob?.batches.flatMap(item => item.generated_draft_ids)[0]
  if (firstGenerated) {
    const version = cases.versions[firstGenerated]
    const endpoint = assets.endpoints.find(item => item.id === version?.endpoint_id)
    if (endpoint) activate(endpoint)
  }
}
async function submitDebug(): Promise<void> {
  if (!context.projectId || !context.sourceRevisionId || !context.environmentRevisionId || !activeVersionId.value) return
  cases.debugExecution = null
  debugOpen.value = true
  await cases.debug({ projectId: context.projectId, sourceRevisionId: context.sourceRevisionId, environmentRevisionId: context.environmentRevisionId, caseVersionId: activeVersionId.value })
}
</script>

<template>
  <section class="workspace workbench-page">
    <header class="page-toolbar"><div><p class="eyebrow">API TEST WORKSPACE</p><h1>接口测试工作台</h1><p class="page-subtitle">选接口，AI 设计，保存草稿后直接调试。</p></div><button class="icon-command" type="button" title="重新读取已保存接口" :disabled="!context.sourceRevisionId || assets.state === 'loading'" @click="context.sourceRevisionId && assets.load(context.sourceRevisionId)"><RefreshCw :size="18" /></button></header>
    <ContextBar :project-id="context.projectId" :source-revision-id="context.sourceRevisionId" :environment-revision-id="context.environmentRevisionId" :loading="context.loading" :saved="!context.error" @save="context.saveContext()" />
    <p v-if="context.error || localError" class="inline-error">{{ context.error || localError }}</p>
    <div class="design-workspace">
      <EndpointTree :endpoints="assets.endpoints" :selected-ids="selectedIds" :state="context.sourceRevisionId ? assets.state : 'empty'" :error="assets.error" @selection-change="selectedIds = $event" @activate="activate" />
      <main class="design-center">
        <EndpointDetail :endpoint="activeEndpoint" />
        <CaseEditor v-if="activeDraft" :model-value="activeDraft" :saving="cases.saving" :saved-message="cases.savedMessage" @update:model-value="updateDraft" @save="saveDraft" />
        <div v-else class="state-message center-empty">选择接口后，可手工编辑或让 AI 生成测试用例。</div>
        <button v-if="activeVersionId" class="debug-command" type="button" @click="submitDebug"><Bug :size="16" />调试当前草稿</button>
      </main>
      <AiAssistant :selected-count="selectedIds.length" :job="cases.aiJob" :error="cases.aiError" @generate="generate" @retry="generate" />
    </div>
    <DebugDrawer v-if="debugOpen" :open="debugOpen" :case-version-id="activeVersionId" :environment-revision-id="context.environmentRevisionId || ''" :running="debugRunning" :result="cases.debugResult" @submit="submitDebug" @adopt="cases.adoptBaseline($event.caseVersionId, $event.executionCaseId)" @close="debugOpen = false" />
  </section>
</template>
