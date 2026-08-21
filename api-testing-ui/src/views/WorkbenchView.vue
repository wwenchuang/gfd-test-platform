<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { Bug, RefreshCw } from 'lucide-vue-next'
import { useRoute, useRouter } from 'vue-router'

import AiAssistant from '../components/AiAssistant.vue'
import CaseEditor from '../components/CaseEditor.vue'
import CaseListPanel from '../components/CaseListPanel.vue'
import ContextBar from '../components/ContextBar.vue'
import DebugDrawer from '../components/DebugDrawer.vue'
import EndpointDetail from '../components/EndpointDetail.vue'
import EndpointTree from '../components/EndpointTree.vue'
import TaskListPanel from '../components/TaskListPanel.vue'
import TaskStatusStrip from '../components/TaskStatusStrip.vue'
import type { ApiEndpoint, ApiTestTask, CaseDraft, CaseVersion, GeneratedCasePreview } from '../api/contracts'
import { useAssetsStore } from '../stores/assets'
import { useCasesStore } from '../stores/cases'
import { useContextStore } from '../stores/context'
import { useTasksStore } from '../stores/tasks'

const context = useContextStore()
const assets = useAssetsStore()
const cases = useCasesStore()
const tasks = useTasksStore()
const route = useRoute()
const router = useRouter()
const selectedIds = ref<string[]>([])
const activeEndpoint = ref<ApiEndpoint | null>(null)
const debugOpen = ref(false)
const localError = ref('')
const taskNameDraft = ref('')
const activeDraft = computed(() => activeEndpoint.value ? cases.draftFor(activeEndpoint.value) : null)
const activeVersionId = computed(() => activeEndpoint.value ? cases.activeVersionByEndpoint[activeEndpoint.value.id] || '' : '')
const allCaseVersions = computed(() => Object.values(cases.versions))
const debugRunning = computed(() => cases.debugPolling)
const selectedEnvironment = computed(() => context.environmentRevisions.find(
  item => item.id === context.environmentRevisionId,
))
const environmentName = computed(() => selectedEnvironment.value?.name || (context.environmentRevisionId ? '任务保存环境' : '未选择环境'))
const environmentLabel = computed(() => selectedEnvironment.value
  ? `${selectedEnvironment.value.name} · v${selectedEnvironment.value.revision}`
  : context.environmentRevisionId ? '任务保存环境 · 已保存任务引用' : '未选择环境')
const taskMatchesSelection = computed(() => Boolean(
  tasks.task
  && tasks.task.project_id === context.projectId
  && tasks.task.source_revision_id === context.sourceRevisionId
  && [...tasks.task.selected_endpoint_ids].sort().join('|') === [...selectedIds.value].sort().join('|'),
))
const taskEnvironmentNames = computed(() => Object.fromEntries(
  context.environmentRevisions.map(item => [item.id, `${item.name} · v${item.revision}`]),
))

onMounted(async () => {
  await Promise.all([context.loadSavedContext(), context.loadOptions()])
  const routeContext = restoreExecutionContextFromRoute()
  if (context.projectId) await tasks.list(context.projectId)
  const restoredTask = context.projectId ? await tasks.restore(context.projectId) : null
  if (restoredTask && !routeContext) {
    const runtimeEnvironmentId = restoredTask.project_id === context.projectId
      ? context.environmentRevisionId || restoredTask.environment_revision_id
      : restoredTask.environment_revision_id
    ensureTaskContextOptions(restoredTask, runtimeEnvironmentId)
    context.restoreExecutionContext({
      project_id: restoredTask.project_id,
      source_revision_id: restoredTask.source_revision_id,
      environment_revision_id: runtimeEnvironmentId,
    })
  }
  const restoredSelection = restoredTask
    && restoredTask.project_id === context.projectId
    && restoredTask.source_revision_id === context.sourceRevisionId
    ? restoredTask.selected_endpoint_ids
    : []
  if (context.sourceRevisionId) await loadSource(context.sourceRevisionId, restoredSelection)
  await restoreDeepLink()
  if (context.projectId) await cases.restoreLatestAiJob(context.projectId)
})

watch(() => tasks.task?.name, name => {
  taskNameDraft.value = name || defaultTaskName()
}, { immediate: true })

function restoreExecutionContextFromRoute(): boolean {
  const projectId = routeValue(route.query.projectId)
  const sourceRevisionId = routeValue(route.query.sourceRevisionId)
  const environmentRevisionId = routeValue(route.query.environmentRevisionId)
  if (!projectId || !sourceRevisionId || !environmentRevisionId) return false
  context.restoreExecutionContext({
    project_id: projectId,
    source_revision_id: sourceRevisionId,
    environment_revision_id: environmentRevisionId,
  })
  return true
}

async function loadSource(sourceRevisionId: string, restoredSelection: string[] = []): Promise<void> {
  localError.value = ''
  try {
    await Promise.all([assets.load(sourceRevisionId), cases.loadSavedCases(sourceRevisionId)])
    activeEndpoint.value = null
    const available = new Set(assets.endpoints.map(item => item.id))
    selectedIds.value = restoredSelection.filter(item => available.has(item))
  } catch (error) {
    localError.value = error instanceof Error ? error.message : '无法读取已保存接口和用例'
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
  if (projectId) void tasks.list(projectId)
}

async function changeSource(sourceRevisionId: string | null): Promise<void> {
  context.selectSourceRevision(sourceRevisionId)
  tasks.clear()
  cases.clearDebug()
  cases.clearAiJob()
  debugOpen.value = false
  if (sourceRevisionId) await loadSource(sourceRevisionId)
  else {
    assets.endpoints = []
    activeEndpoint.value = null
  }
}

function changeEnvironment(environmentRevisionId: string | null): void {
  context.selectEnvironmentRevision(environmentRevisionId)
  cases.clearDebug()
  cases.clearAiJob()
  debugOpen.value = false
}

async function selectTask(taskId: string): Promise<void> {
  localError.value = ''
  const task = tasks.select(taskId)
  if (!task) return
  cases.clearDebug()
  cases.clearAiJob()
  debugOpen.value = false
  const runtimeEnvironmentId = task.project_id === context.projectId
    ? context.environmentRevisionId || task.environment_revision_id
    : task.environment_revision_id
  ensureTaskContextOptions(task, runtimeEnvironmentId)
  context.restoreExecutionContext({
    project_id: task.project_id,
    source_revision_id: task.source_revision_id,
    environment_revision_id: runtimeEnvironmentId,
  })
  await loadSource(task.source_revision_id, task.selected_endpoint_ids)
  const endpoint = assets.endpoints.find(item => task.selected_endpoint_ids.includes(item.id))
  activeEndpoint.value = endpoint || null
}

function activate(endpoint: ApiEndpoint): void {
  activeEndpoint.value = endpoint
  cases.draftFor(endpoint)
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

function startNewTask(): void {
  tasks.clear()
  cases.clearDebug()
  cases.clearAiJob()
  debugOpen.value = false
  localError.value = ''
  selectedIds.value = []
  activeEndpoint.value = null
  taskNameDraft.value = defaultTaskName()
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

async function deleteCaseVersion(version: CaseVersion): Promise<void> {
  if (!version) return
  const confirmed = window.confirm(`删除用例“${version.name}”？历史执行记录和已采纳基线证据会保留。`)
  if (!confirmed) return
  localError.value = ''
  try {
    const endpointId = version.endpoint_id
    await cases.archiveCase(endpointId, version.id)
    if (activeEndpoint.value?.id === endpointId && !cases.versionIdsByEndpoint[endpointId]?.length) cases.draftFor(activeEndpoint.value)
    if (context.projectId) await tasks.restore(context.projectId)
  } catch (error) {
    localError.value = error instanceof Error ? error.message : '用例删除失败'
  }
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
  if (activeEndpoint.value) {
    cases.updateDraft(activeEndpoint.value.id, draft)
    cases.clearDebug()
    debugOpen.value = false
  }
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
async function generate(intent: string): Promise<void> {
  if (!context.environmentRevisionId) { localError.value = '请先选择执行环境'; return }
  cases.clearAiJob()
  const task = await saveCurrentTask()
  if (!task) return
  await cases.generate([...task.selected_endpoint_ids], context.environmentRevisionId, intent, task.id)
  if (context.projectId) await tasks.restore(context.projectId)
  const firstGenerated = cases.aiJob?.batches.flatMap(item => item.generated_draft_ids)[0]
  if (firstGenerated) {
    const version = cases.versions[firstGenerated]
    const endpoint = assets.endpoints.find(item => item.id === version?.endpoint_id)
    if (endpoint) activate(endpoint)
  }
}
async function generateBasicPositive(): Promise<void> {
  if (!context.environmentRevisionId) { localError.value = '请先选择执行环境'; return }
  const task = await saveCurrentTask()
  if (!task) return
  localError.value = ''
  try {
    const previews = await cases.previewBasicPositive([...task.selected_endpoint_ids], context.environmentRevisionId, task.id)
    const firstPreview = previews[0]
    const endpoint = assets.endpoints.find(item => item.id === firstPreview?.endpoint_id)
    if (endpoint && firstPreview) {
      activeEndpoint.value = endpoint
      cases.setDraftFromGeneratedPreview(firstPreview.id)
    }
  } catch (error) {
    localError.value = error instanceof Error ? error.message : '基础正向用例生成失败'
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
    const firstVersion = versions[0]
    if (firstVersion) editCaseVersion(firstVersion)
    if (context.projectId) await tasks.restore(context.projectId)
  } catch (error) {
    localError.value = error instanceof Error ? error.message : '候选用例批量保存失败'
  }
}

function discardGeneratedPreview(previewId: string): void {
  const wasActive = cases.activeGeneratedPreviewId === previewId
  cases.discardGeneratedPreview(previewId)
  if (wasActive && activeEndpoint.value && !cases.drafts[activeEndpoint.value.id]) {
    activeEndpoint.value = null
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

function toggleCaseScope(endpointId: string): void {
  selectedIds.value = selectedIds.value.includes(endpointId)
    ? selectedIds.value.filter(item => item !== endpointId)
    : [...selectedIds.value, endpointId]
}
async function submitDebug(): Promise<void> {
  if (!context.projectId || !context.sourceRevisionId || !context.environmentRevisionId || !activeEndpoint.value) return
  const endpointId = activeEndpoint.value.id
  localError.value = ''
  let version
  try {
    version = await cases.saveForDebug(endpointId, context.environmentRevisionId)
  } catch (error) {
    localError.value = error instanceof Error ? error.message : '当前草稿保存或校验失败'
    return
  }
  if (!selectedIds.value.includes(endpointId)) selectedIds.value = [...selectedIds.value, endpointId]
  const task = await saveCurrentTask()
  if (!task) return
  cases.debugExecution = null
  debugOpen.value = true
  localError.value = ''
  try {
    await cases.debug({ projectId: context.projectId, sourceRevisionId: context.sourceRevisionId, environmentRevisionId: context.environmentRevisionId, caseVersionId: version.id, taskId: task.id })
    await tasks.restore(context.projectId)
  } catch (error) {
    cases.debugError = error instanceof Error ? error.message : '调试任务创建失败'
  }
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
    const name = taskNameDraft.value.trim() || defaultTaskName()
    return await tasks.saveSelection({
      projectId: context.projectId,
      sourceRevisionId: context.sourceRevisionId,
      environmentRevisionId: context.environmentRevisionId,
    }, selectedIds.value, name)
  } catch (error) {
    localError.value = error instanceof Error ? error.message : '测试任务保存失败'
    return null
  }
}

async function renameCurrentTask(): Promise<void> {
  if (!tasks.task) {
    localError.value = '请先保存任务后再修改任务名称'
    return
  }
  localError.value = ''
  try {
    await tasks.rename(tasks.task.id, taskNameDraft.value)
  } catch (error) {
    localError.value = error instanceof Error ? error.message : '任务名称保存失败'
  }
}

async function runCurrentTask(): Promise<void> {
  if (!context.environmentRevisionId) {
    localError.value = '请选择本次执行环境'
    return
  }
  if (!taskMatchesSelection.value && !await saveCurrentTask()) return
  try {
    const execution = await tasks.runCurrent(context.environmentRevisionId)
    await router.push({ name: 'runs', query: { executionId: execution.id } })
  } catch (error) {
    localError.value = error instanceof Error ? error.message : '测试任务执行失败'
  }
}

async function runSavedTask(taskId: string): Promise<void> {
  await selectTask(taskId)
  await runCurrentTask()
}

async function deleteTask(task: ApiTestTask): Promise<void> {
  const confirmed = window.confirm(`删除任务“${task.name}”？任务关联的用例、基线和历史执行记录会保留。`)
  if (!confirmed) return
  localError.value = ''
  try {
    await tasks.remove(task.id)
    if (tasks.task?.id !== task.id && activeEndpoint.value) return
    if (!tasks.task) {
      selectedIds.value = []
      activeEndpoint.value = null
      taskNameDraft.value = defaultTaskName()
    }
  } catch (error) {
    localError.value = error instanceof Error ? error.message : '任务删除失败'
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

function routeValue(value: unknown): string {
  return typeof value === 'string' ? value : ''
}

function ensureTaskContextOptions(task: ApiTestTask, environmentRevisionId: string | null): void {
  ensureSourceRevisionOption(task)
  if (environmentRevisionId) ensureEnvironmentRevisionOption(task, environmentRevisionId)
}

function ensureSourceRevisionOption(task: ApiTestTask): void {
  if (context.sourceRevisions.some(item => item.id === task.source_revision_id)) return
  context.sourceRevisions = [
    ...context.sourceRevisions,
    {
      id: task.source_revision_id,
      source_id: task.source_revision_id,
      project_id: task.project_id,
      name: '当前任务接口版本',
      revision_number: 0,
      endpoint_count: task.selected_endpoint_ids.length,
      source_status: 'active',
    },
  ]
}

function ensureEnvironmentRevisionOption(task: ApiTestTask, environmentRevisionId: string): void {
  if (context.environmentRevisions.some(item => item.id === environmentRevisionId)) return
  context.environmentRevisions = [
    ...context.environmentRevisions,
    {
      id: environmentRevisionId,
      environment_id: environmentRevisionId,
      project_id: task.project_id,
      name: environmentRevisionId === task.environment_revision_id ? '任务保存环境' : '当前执行环境',
      revision: 0,
      status: 'active',
    },
  ]
}

function defaultTaskName(): string {
  const projectName = context.projects.find(item => item.id === context.projectId)?.name || 'API'
  return `${projectName}接口测试`
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
      @update:environment-revision-id="changeEnvironment"
      @save="saveScope"
    />
    <TaskStatusStrip
      v-model:task-name-draft="taskNameDraft"
      :task="tasks.task"
      :selected-count="selectedIds.length"
      :environment-name="environmentName"
      :saving="tasks.saving"
      :running="tasks.running"
      @rename-task="renameCurrentTask"
      @new="startNewTask"
      @save="saveCurrentTask"
      @run="runCurrentTask"
    />
    <p v-if="context.error || tasks.error || localError" class="inline-error">{{ context.error || tasks.error || localError }}</p>
    <div class="workbench-shell">
      <TaskListPanel
        :tasks="tasks.tasks"
        :active-task-id="tasks.task?.id || ''"
        :environment-names="taskEnvironmentNames"
        :loading="tasks.loading"
        :saving="tasks.saving"
        :running="tasks.running"
        @new="startNewTask"
        @select="selectTask"
        @run="runSavedTask"
        @delete="deleteTask"
      />
      <div class="design-workspace">
        <div class="scope-sidebar">
          <EndpointTree :endpoints="assets.endpoints" :selected-ids="selectedIds" :state="context.sourceRevisionId ? assets.state : 'empty'" :error="assets.error" @selection-change="selectedIds = $event" @activate="activate" />
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
        </div>
        <main class="design-center">
          <EndpointDetail :endpoint="activeEndpoint" />
          <CaseEditor v-if="activeDraft" :model-value="activeDraft" :saving="cases.saving" :saved-message="cases.savedMessage" :validation-errors="cases.validationErrors" :validation-warnings="cases.validationWarnings" @update:model-value="updateDraft" @save="saveDraft" />
          <div v-else class="state-message center-empty">选择接口后，可手工编辑或让 AI 生成测试用例。</div>
          <button v-if="activeDraft" class="debug-command" type="button" :disabled="cases.saving || debugRunning" @click="submitDebug"><Bug :size="16" />{{ cases.saving ? '正在保存…' : debugRunning ? '调试中…' : '保存并调试' }}</button>
        </main>
        <AiAssistant :selected-count="selectedIds.length" :job="cases.aiJob" :error="cases.aiError" :polling="cases.aiPolling" :can-resume="cases.aiCanResume" :basic-generating="cases.basicGenerating" @generate-basic="generateBasicPositive" @generate="generate" @retry="generate" @resume="cases.resumeAiJob()" />
      </div>
    </div>
    <DebugDrawer v-if="debugOpen" :open="debugOpen" :case-version-id="activeVersionId" :environment-revision-id="context.environmentRevisionId || ''" :environment-label="environmentLabel" :running="debugRunning" :can-resume="cases.debugCanResume" :result="cases.debugResult" :error="cases.debugError" :baseline-adopting="cases.baselineAdopting" :baseline-message="cases.baselineMessage" :baseline-error="cases.baselineError" @submit="submitDebug" @resume="cases.resumeDebug()" @adopt="adoptBaseline" @close="debugOpen = false" />
  </section>
</template>
