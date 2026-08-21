<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { Bug, ListChecks, RefreshCw } from 'lucide-vue-next'
import { useRouter } from 'vue-router'

import type { ApiEndpoint, CaseDraft, CaseVersion, GeneratedCasePreview } from '../api/contracts'
import CaseEditor from '../components/CaseEditor.vue'
import CaseListPanel from '../components/CaseListPanel.vue'
import ContextBar from '../components/ContextBar.vue'
import DebugDrawer from '../components/DebugDrawer.vue'
import EndpointDetail from '../components/EndpointDetail.vue'
import { useAssetsStore } from '../stores/assets'
import { useCasesStore } from '../stores/cases'
import { useContextStore } from '../stores/context'
import { useTasksStore } from '../stores/tasks'

const context = useContextStore()
const assets = useAssetsStore()
const cases = useCasesStore()
const tasks = useTasksStore()
const router = useRouter()

const selectedIds = ref<string[]>([])
const activeEndpoint = ref<ApiEndpoint | null>(null)
const debugOpen = ref(false)
const localError = ref('')

const activeDraft = computed(() => activeEndpoint.value ? cases.draftFor(activeEndpoint.value) : null)
const activeVersionId = computed(() => activeEndpoint.value ? cases.activeVersionByEndpoint[activeEndpoint.value.id] || '' : '')
const allCaseVersions = computed(() => Object.values(cases.versions))
const debugRunning = computed(() => cases.debugPolling)
const selectedEnvironment = computed(() => context.environmentRevisions.find(item => item.id === context.environmentRevisionId))
const environmentLabel = computed(() => selectedEnvironment.value
  ? `${selectedEnvironment.value.name} · v${selectedEnvironment.value.revision}`
  : context.environmentRevisionId ? '任务保存环境 · 已保存任务引用' : '未选择环境')
const activeTaskName = computed(() => tasks.task?.name || '未绑定任务')

onMounted(async () => {
  await Promise.all([context.loadSavedContext(), context.loadOptions()])
  if (context.projectId) {
    await tasks.list(context.projectId)
    const restored = await tasks.restore(context.projectId)
    if (restored?.source_revision_id === context.sourceRevisionId) selectedIds.value = [...restored.selected_endpoint_ids]
  }
  if (context.sourceRevisionId) await loadSource(context.sourceRevisionId)
})

async function loadSource(sourceRevisionId: string): Promise<void> {
  localError.value = ''
  try {
    await Promise.all([assets.load(sourceRevisionId), cases.loadSavedCases(sourceRevisionId)])
    activeEndpoint.value = null
    const available = new Set(assets.endpoints.map(item => item.id))
    selectedIds.value = selectedIds.value.filter(item => available.has(item))
  } catch (error) {
    localError.value = error instanceof Error ? error.message : '无法读取接口和用例'
  }
}

function changeProject(projectId: string | null): void {
  context.selectProject(projectId)
  tasks.clear()
  tasks.tasks = []
  cases.clearDebug()
  cases.clearAiJob()
  debugOpen.value = false
  activeEndpoint.value = null
  selectedIds.value = []
  assets.endpoints = []
  if (projectId) void tasks.list(projectId)
}

async function changeSource(sourceRevisionId: string | null): Promise<void> {
  context.selectSourceRevision(sourceRevisionId)
  tasks.clear()
  cases.clearDebug()
  cases.clearAiJob()
  debugOpen.value = false
  activeEndpoint.value = null
  selectedIds.value = []
  if (sourceRevisionId) await loadSource(sourceRevisionId)
  else assets.endpoints = []
}

function changeEnvironment(environmentRevisionId: string | null): void {
  context.selectEnvironmentRevision(environmentRevisionId)
  cases.clearDebug()
  cases.clearAiJob()
  debugOpen.value = false
}

async function saveScope(): Promise<void> {
  localError.value = ''
  try {
    await context.saveContext()
    if (context.error) throw new Error(context.error)
  } catch (error) {
    localError.value = error instanceof Error ? error.message : '测试范围保存失败'
  }
}

function editCaseVersion(version: CaseVersion): void {
  const endpoint = assets.endpoints.find(item => item.id === version.endpoint_id)
  if (!endpoint) {
    localError.value = '该用例对应的接口不在当前接口版本中'
    return
  }
  activeEndpoint.value = endpoint
  if (version.id !== activeVersionId.value) {
    cases.clearDebug()
    debugOpen.value = false
    cases.setActiveVersion(endpoint.id, version.id)
  }
}

function editGeneratedPreview(preview: GeneratedCasePreview): void {
  const endpoint = assets.endpoints.find(item => item.id === preview.endpoint_id)
  if (!endpoint) {
    localError.value = '该候选用例对应的接口不在当前接口版本中'
    return
  }
  activeEndpoint.value = endpoint
  cases.setDraftFromGeneratedPreview(preview.id)
  debugOpen.value = false
}

function updateDraft(draft: CaseDraft): void {
  if (!activeEndpoint.value) return
  cases.updateDraft(activeEndpoint.value.id, draft)
  cases.clearDebug()
  debugOpen.value = false
}

async function saveDraft() {
  if (!activeEndpoint.value) return null
  localError.value = ''
  try {
    if (cases.activeGeneratedPreviewId) {
      return await cases.saveGeneratedPreview(cases.activeGeneratedPreviewId, activeDraft.value || undefined)
    }
    return await cases.save(activeEndpoint.value.id, context.environmentRevisionId || undefined)
  } catch (error) {
    localError.value = error instanceof Error ? error.message : '草稿保存失败'
    return null
  }
}

async function saveGeneratedPreview(preview: GeneratedCasePreview): Promise<void> {
  localError.value = ''
  try {
    const draft = cases.activeGeneratedPreviewId === preview.id ? activeDraft.value || preview.case : preview.case
    const version = await cases.saveGeneratedPreview(preview.id, draft)
    editCaseVersion(version)
    if (context.projectId) await tasks.restore(context.projectId)
  } catch (error) {
    localError.value = error instanceof Error ? error.message : '候选用例保存失败'
  }
}

async function saveAllGeneratedPreviews(): Promise<void> {
  localError.value = ''
  try {
    const overrides = cases.activeGeneratedPreviewId && activeDraft.value
      ? { [cases.activeGeneratedPreviewId]: activeDraft.value }
      : {}
    const versions = await cases.saveAllGeneratedPreviews(overrides)
    if (versions[0]) editCaseVersion(versions[0])
    if (context.projectId) await tasks.restore(context.projectId)
  } catch (error) {
    localError.value = error instanceof Error ? error.message : '候选用例批量保存失败'
  }
}

function discardGeneratedPreview(previewId: string): void {
  const wasActive = cases.activeGeneratedPreviewId === previewId
  cases.discardGeneratedPreview(previewId)
  if (wasActive && activeEndpoint.value && !cases.drafts[activeEndpoint.value.id]) activeEndpoint.value = null
}

async function deleteCaseVersion(version: CaseVersion): Promise<void> {
  const confirmed = window.confirm(`删除用例“${version.name}”？历史执行记录和已采纳基线证据会保留。`)
  if (!confirmed) return
  localError.value = ''
  try {
    const endpointId = version.endpoint_id
    await cases.archiveCase(endpointId, version.id)
    if (activeEndpoint.value?.id === endpointId && !cases.versionIdsByEndpoint[endpointId]?.length) {
      activeEndpoint.value = null
    }
    if (context.projectId) await tasks.restore(context.projectId)
  } catch (error) {
    localError.value = error instanceof Error ? error.message : '用例删除失败'
  }
}

function toggleCaseScope(endpointId: string): void {
  selectedIds.value = selectedIds.value.includes(endpointId)
    ? selectedIds.value.filter(item => item !== endpointId)
    : [...selectedIds.value, endpointId]
}

async function saveCurrentTask() {
  if (!context.projectId || !context.sourceRevisionId || !context.environmentRevisionId) {
    localError.value = '请先选择接口项目、接口版本和执行环境'
    return null
  }
  if (!selectedIds.value.length) {
    localError.value = '请至少选择一个接口'
    return null
  }
  localError.value = ''
  try {
    await context.saveContext()
    if (context.error) throw new Error(context.error)
    return await tasks.saveSelection({
      projectId: context.projectId,
      sourceRevisionId: context.sourceRevisionId,
      environmentRevisionId: context.environmentRevisionId,
    }, selectedIds.value, tasks.task?.name || defaultTaskName())
  } catch (error) {
    localError.value = error instanceof Error ? error.message : '任务范围保存失败'
    return null
  }
}

async function runCaseVersion(version: CaseVersion): Promise<void> {
  if (!context.projectId || !context.sourceRevisionId || !context.environmentRevisionId) {
    localError.value = '请先选择接口项目、接口版本和执行环境'
    return
  }
  editCaseVersion(version)
  if (!selectedIds.value.includes(version.endpoint_id)) selectedIds.value = [...selectedIds.value, version.endpoint_id]
  const task = await saveCurrentTask()
  if (!task) return
  cases.debugExecution = null
  debugOpen.value = true
  localError.value = ''
  try {
    await cases.debug({
      projectId: context.projectId,
      sourceRevisionId: context.sourceRevisionId,
      environmentRevisionId: context.environmentRevisionId,
      caseVersionId: version.id,
      taskId: task.id,
    })
    await tasks.restore(context.projectId)
  } catch (error) {
    cases.debugError = error instanceof Error ? error.message : '用例执行失败'
  }
}

async function submitDebug(): Promise<void> {
  if (!context.projectId || !context.sourceRevisionId || !context.environmentRevisionId || !activeEndpoint.value) return
  localError.value = ''
  let version
  try {
    version = await cases.saveForDebug(activeEndpoint.value.id, context.environmentRevisionId)
  } catch (error) {
    localError.value = error instanceof Error ? error.message : '当前草稿保存或校验失败'
    return
  }
  if (!selectedIds.value.includes(activeEndpoint.value.id)) selectedIds.value = [...selectedIds.value, activeEndpoint.value.id]
  const task = await saveCurrentTask()
  if (!task) return
  cases.debugExecution = null
  debugOpen.value = true
  localError.value = ''
  try {
    await cases.debug({
      projectId: context.projectId,
      sourceRevisionId: context.sourceRevisionId,
      environmentRevisionId: context.environmentRevisionId,
      caseVersionId: version.id,
      taskId: task.id,
    })
    await tasks.restore(context.projectId)
  } catch (error) {
    cases.debugError = error instanceof Error ? error.message : '调试任务创建失败'
  }
}

async function adoptBaseline(input: { caseVersionId: string; executionCaseId: string }): Promise<void> {
  await cases.adoptBaseline(input.caseVersionId, input.executionCaseId)
  if (!cases.baselineError && context.projectId) {
    try { await tasks.restore(context.projectId) } catch (error) {
      localError.value = error instanceof Error ? `基线已采纳，但任务状态刷新失败：${error.message}` : '基线已采纳，但任务状态刷新失败'
    }
  }
}

async function refreshCases(): Promise<void> {
  if (context.sourceRevisionId) await loadSource(context.sourceRevisionId)
}

async function openWorkbenchForTask(): Promise<void> {
  if (!tasks.task) return
  await router.push({
    name: 'workbench',
    query: {
      taskId: tasks.task.id,
      projectId: tasks.task.project_id,
      sourceRevisionId: tasks.task.source_revision_id,
      environmentRevisionId: context.environmentRevisionId || tasks.task.environment_revision_id,
    },
  })
}

function defaultTaskName(): string {
  const projectName = context.projects.find(item => item.id === context.projectId)?.name || 'API'
  return `${projectName}接口测试`
}
</script>

<template>
  <section class="workspace management-page cases-page" data-testid="cases-page">
    <header class="page-toolbar">
      <div>
        <p class="eyebrow">API CASE MANAGEMENT</p>
        <h1>用例管理</h1>
        <p class="page-subtitle">集中管理已生成和已保存的 API 用例，支持编辑、删除、执行和加入任务范围。</p>
      </div>
      <button class="icon-command" type="button" title="重新读取用例" :disabled="!context.sourceRevisionId || assets.state === 'loading'" @click="refreshCases">
        <RefreshCw :size="18" />
      </button>
    </header>
    <ContextBar
      :projects="context.projects"
      :source-revisions="context.sourceRevisions"
      :environment-revisions="context.environmentRevisions"
      :project-id="context.projectId"
      :source-revision-id="context.sourceRevisionId"
      :environment-revision-id="context.environmentRevisionId"
      :loading="context.loading || context.optionsLoading"
      :saved="context.isSaved"
      save-label="保存管理范围"
      saved-label="管理范围已保存"
      @update:project-id="changeProject"
      @update:source-revision-id="changeSource"
      @update:environment-revision-id="changeEnvironment"
      @save="saveScope"
    />
    <p v-if="context.error || tasks.error || localError" class="inline-error">{{ context.error || tasks.error || localError }}</p>
    <div class="management-shell case-management-shell" data-testid="case-management-shell">
      <CaseListPanel
        :endpoints="assets.endpoints"
        :versions="allCaseVersions"
        :generated-previews="cases.generatedPreviews"
        :active-version-id="activeVersionId"
        :active-preview-id="cases.activeGeneratedPreviewId"
        :selected-endpoint-ids="selectedIds"
        :saving="cases.saving || cases.basicGenerating"
        :running="debugRunning"
        @edit-version="editCaseVersion"
        @run-version="runCaseVersion"
        @delete-version="deleteCaseVersion"
        @toggle-scope="toggleCaseScope"
        @edit-preview="editGeneratedPreview"
        @save-preview="saveGeneratedPreview"
        @discard-preview="discardGeneratedPreview"
        @save-all-previews="saveAllGeneratedPreviews"
      />
      <main class="management-detail">
        <header class="management-detail-head">
          <div>
            <p>当前任务：{{ activeTaskName }}</p>
            <h2>{{ activeDraft?.name || activeEndpoint?.summary || '选择左侧用例' }}</h2>
          </div>
          <div class="detail-action-row">
            <button class="secondary-command" type="button" :disabled="!selectedIds.length || tasks.saving" @click="saveCurrentTask">
              <ListChecks :size="15" />保存任务范围
            </button>
            <button class="secondary-command" type="button" :disabled="!tasks.task" @click="openWorkbenchForTask">编辑任务范围</button>
          </div>
        </header>
        <div class="management-detail-body">
          <EndpointDetail :endpoint="activeEndpoint" />
          <CaseEditor
            v-if="activeDraft"
            :model-value="activeDraft"
            :saving="cases.saving"
            :saved-message="cases.savedMessage"
            :validation-errors="cases.validationErrors"
            :validation-warnings="cases.validationWarnings"
            @update:model-value="updateDraft"
            @save="saveDraft"
          />
          <div v-else class="state-message center-empty">从左侧选择一个用例后，可查看接口信息并编辑测试数据。</div>
          <button v-if="activeDraft" class="debug-command" type="button" :disabled="cases.saving || debugRunning" @click="submitDebug">
            <Bug :size="16" />{{ cases.saving ? '正在保存…' : debugRunning ? '执行中…' : '保存并执行' }}
          </button>
        </div>
      </main>
    </div>
    <DebugDrawer
      v-if="debugOpen"
      :open="debugOpen"
      :case-version-id="activeVersionId"
      :environment-revision-id="context.environmentRevisionId || ''"
      :environment-label="environmentLabel"
      :running="debugRunning"
      :can-resume="cases.debugCanResume"
      :result="cases.debugResult"
      :error="cases.debugError"
      :baseline-adopting="cases.baselineAdopting"
      :baseline-message="cases.baselineMessage"
      :baseline-error="cases.baselineError"
      @submit="submitDebug"
      @resume="cases.resumeDebug()"
      @adopt="adoptBaseline"
      @close="debugOpen = false"
    />
  </section>
</template>
