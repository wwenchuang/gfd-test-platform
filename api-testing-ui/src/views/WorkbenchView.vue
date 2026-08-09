<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { Bug, RefreshCw } from 'lucide-vue-next'
import { useRoute } from 'vue-router'

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
const route = useRoute()
const selectedIds = ref<string[]>([])
const activeEndpoint = ref<ApiEndpoint | null>(null)
const debugOpen = ref(false)
const localError = ref('')
const activeDraft = computed(() => activeEndpoint.value ? cases.draftFor(activeEndpoint.value) : null)
const activeVersionId = computed(() => activeEndpoint.value ? cases.activeVersionByEndpoint[activeEndpoint.value.id] || '' : '')
const activeVersions = computed(() => activeEndpoint.value
  ? (cases.versionIdsByEndpoint[activeEndpoint.value.id] || []).map(id => cases.versions[id]).filter(Boolean)
  : [])
const debugRunning = computed(() => cases.debugPolling)

onMounted(async () => {
  await Promise.all([context.loadSavedContext(), context.loadOptions()])
  restoreExecutionContextFromRoute()
  if (context.sourceRevisionId) await loadSource(context.sourceRevisionId)
  await restoreDeepLink()
  if (context.projectId) await cases.restoreLatestAiJob(context.projectId)
})

function restoreExecutionContextFromRoute(): void {
  const projectId = routeValue(route.query.projectId)
  const sourceRevisionId = routeValue(route.query.sourceRevisionId)
  const environmentRevisionId = routeValue(route.query.environmentRevisionId)
  if (!projectId || !sourceRevisionId || !environmentRevisionId) return
  context.restoreExecutionContext({
    project_id: projectId,
    source_revision_id: sourceRevisionId,
    environment_revision_id: environmentRevisionId,
  })
}

async function loadSource(sourceRevisionId: string): Promise<void> {
  localError.value = ''
  try {
    await Promise.all([assets.load(sourceRevisionId), cases.loadSavedCases(sourceRevisionId)])
    activeEndpoint.value = null
    selectedIds.value = []
  } catch (error) {
    localError.value = error instanceof Error ? error.message : '无法读取已保存接口和用例'
  }
}

function changeProject(projectId: string | null): void {
  context.selectProject(projectId)
  activeEndpoint.value = null
  selectedIds.value = []
}

async function changeSource(sourceRevisionId: string | null): Promise<void> {
  context.selectSourceRevision(sourceRevisionId)
  if (sourceRevisionId) await loadSource(sourceRevisionId)
  else {
    assets.endpoints = []
    activeEndpoint.value = null
  }
}

function activate(endpoint: ApiEndpoint): void {
  activeEndpoint.value = endpoint
  cases.draftFor(endpoint)
}

async function restoreDeepLink(): Promise<void> {
  const endpointId = routeValue(route.query.endpointId)
  const caseVersionId = routeValue(route.query.caseVersionId)
  if (!endpointId) return
  const endpoint = assets.endpoints.find(item => item.id === endpointId)
  if (!endpoint) {
    localError.value = '执行记录对应的接口不在当前接口版本中'
    return
  }
  if (caseVersionId) {
    try { await cases.loadVersion(caseVersionId) } catch (error) {
      localError.value = error instanceof Error ? error.message : '无法恢复执行记录对应的用例版本'
      return
    }
  }
  activate(endpoint)
}
function updateDraft(draft: CaseDraft): void {
  if (activeEndpoint.value) cases.updateDraft(activeEndpoint.value.id, draft)
}
async function saveDraft(): Promise<void> {
  if (!activeEndpoint.value) return
  localError.value = ''
  try { await cases.save(activeEndpoint.value.id, context.environmentRevisionId || undefined) } catch (error) { localError.value = error instanceof Error ? error.message : '草稿保存失败' }
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
  localError.value = ''
  try {
    await cases.debug({ projectId: context.projectId, sourceRevisionId: context.sourceRevisionId, environmentRevisionId: context.environmentRevisionId, caseVersionId: activeVersionId.value })
  } catch (error) {
    cases.debugError = error instanceof Error ? error.message : '调试任务创建失败'
  }
}

function routeValue(value: unknown): string {
  return typeof value === 'string' ? value : ''
}
</script>

<template>
  <section class="workspace workbench-page">
    <header class="page-toolbar"><div><p class="eyebrow">API TEST WORKSPACE</p><h1>接口测试工作台</h1><p class="page-subtitle">选接口，AI 设计，保存草稿后直接调试。</p></div><button class="icon-command" type="button" title="重新读取已保存接口" :disabled="!context.sourceRevisionId || assets.state === 'loading'" @click="context.sourceRevisionId && assets.load(context.sourceRevisionId)"><RefreshCw :size="18" /></button></header>
    <ContextBar
      :projects="context.projects"
      :source-revisions="context.sourceRevisions"
      :environment-revisions="context.environmentRevisions"
      :project-id="context.projectId"
      :source-revision-id="context.sourceRevisionId"
      :environment-revision-id="context.environmentRevisionId"
      :loading="context.loading || context.optionsLoading"
      :saved="context.isSaved"
      @update:project-id="changeProject"
      @update:source-revision-id="changeSource"
      @update:environment-revision-id="context.selectEnvironmentRevision($event)"
      @save="context.saveContext()"
    />
    <p v-if="context.error || localError" class="inline-error">{{ context.error || localError }}</p>
    <div class="design-workspace">
      <EndpointTree :endpoints="assets.endpoints" :selected-ids="selectedIds" :state="context.sourceRevisionId ? assets.state : 'empty'" :error="assets.error" @selection-change="selectedIds = $event" @activate="activate" />
      <main class="design-center">
        <EndpointDetail :endpoint="activeEndpoint" />
        <div v-if="activeEndpoint && activeVersions.length" class="case-version-picker"><label>已保存用例<select :value="activeVersionId" @change="cases.setActiveVersion(activeEndpoint!.id, ($event.target as HTMLSelectElement).value)"><option v-for="version in activeVersions" :key="version.id" :value="version.id">{{ version.name }} · v{{ version.version }} · {{ version.origin === 'ai' ? 'AI' : '手工' }}</option></select></label><span>{{ activeVersions.length }} 个用例</span></div>
        <CaseEditor v-if="activeDraft" :model-value="activeDraft" :saving="cases.saving" :saved-message="cases.savedMessage" :validation-errors="cases.validationErrors" :validation-warnings="cases.validationWarnings" @update:model-value="updateDraft" @save="saveDraft" />
        <div v-else class="state-message center-empty">选择接口后，可手工编辑或让 AI 生成测试用例。</div>
        <button v-if="activeVersionId" class="debug-command" type="button" @click="submitDebug"><Bug :size="16" />调试当前草稿</button>
      </main>
      <AiAssistant :selected-count="selectedIds.length" :job="cases.aiJob" :error="cases.aiError" :polling="cases.aiPolling" :can-resume="cases.aiCanResume" @generate="generate" @retry="generate" @resume="cases.resumeAiJob()" />
    </div>
    <DebugDrawer v-if="debugOpen" :open="debugOpen" :case-version-id="activeVersionId" :environment-revision-id="context.environmentRevisionId || ''" :running="debugRunning" :can-resume="cases.debugCanResume" :result="cases.debugResult" :error="cases.debugError" @submit="submitDebug" @resume="cases.resumeDebug()" @adopt="cases.adoptBaseline($event.caseVersionId, $event.executionCaseId)" @close="debugOpen = false" />
  </section>
</template>
