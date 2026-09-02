<script setup lang="ts">
import { computed, nextTick, onMounted, ref, watch } from 'vue'
import { AlertTriangle, ListTree, PencilLine, RefreshCw, Sparkles } from 'lucide-vue-next'
import { RouterLink, useRoute, useRouter } from 'vue-router'

import AiAssistant from '../components/AiAssistant.vue'
import CaseEditor from '../components/CaseEditor.vue'
import ContextBar from '../components/ContextBar.vue'
import DebugDrawer from '../components/DebugDrawer.vue'
import EndpointDetail from '../components/EndpointDetail.vue'
import EndpointTree from '../components/EndpointTree.vue'
import TaskStatusStrip from '../components/TaskStatusStrip.vue'
import { ApiClient, apiClient } from '../api/client'
import type { ApiEndpoint, ApiTestTask, CaseDraft, CaseVersion } from '../api/contracts'
import { useAssetsStore } from '../stores/assets'
import { useCasesStore } from '../stores/cases'
import { useContextStore } from '../stores/context'
import { useTasksStore } from '../stores/tasks'
import { buildCaseDependencyOptions } from '../utils/caseDependencyOptions'
import { confirmApiExecution } from '../utils/executionConfirmation'

const context = useContextStore()
const assets = useAssetsStore()
const cases = useCasesStore()
const tasks = useTasksStore()
const route = useRoute()
const router = useRouter()
const selectedIds = ref<string[]>([])
const endpointTreeTab = ref<'all' | 'selected'>('all')
const activeEndpoint = ref<ApiEndpoint | null>(null)
const debugOpen = ref(false)
const localError = ref('')
const workspaceRestoring = ref(true)
const workspaceRestoreFailed = ref(false)
const taskNameDraft = ref('')
const outdatedRestoredTask = ref<ApiTestTask | null>(null)
const mobilePane = ref<'scope' | 'editor' | 'ai'>('scope')
const caseEditor = ref<{ editStep(target: { stage: 'setup' | 'main' | 'cleanup'; index: number }): Promise<void> } | null>(null)
const activeDraft = computed(() => activeEndpoint.value ? cases.draftFor(activeEndpoint.value) : null)
const activeVersionId = computed(() => activeEndpoint.value ? cases.activeVersionByEndpoint[activeEndpoint.value.id] || '' : '')
const dependencyOptions = computed(() => buildCaseDependencyOptions(
  Object.values(cases.versions),
  assets.endpoints,
  activeVersionId.value,
))
const debugRunning = computed(() => cases.debugPolling)
const selectedEnvironment = computed(() => context.environmentRevisions.find(
  item => item.id === context.environmentRevisionId,
))
const environmentName = computed(() => selectedEnvironment.value?.name || (context.environmentRevisionId ? '任务保存环境' : '未选择环境'))
const environmentLabel = computed(() => selectedEnvironment.value
  ? `${selectedEnvironment.value.name} · v${selectedEnvironment.value.revision}`
  : context.environmentRevisionId ? '任务保存环境 · 已保存任务引用' : '未选择环境')
const currentSourceLabel = computed(() => sourceRevisionLabel(context.sourceRevisionId))
const outdatedTaskSourceLabel = computed(() => sourceRevisionLabel(outdatedRestoredTask.value?.source_revision_id || null))
const taskMatchesSelection = computed(() => Boolean(
  tasks.task
  && tasks.task.project_id === context.projectId
  && tasks.task.source_revision_id === context.sourceRevisionId
  && [...tasks.task.selected_endpoint_ids].sort().join('|') === [...selectedIds.value].sort().join('|'),
))
const editingSavedCaseFromLink = computed(() => Boolean(
  routeValue(route.query.caseVersionId)
  && activeEndpoint.value?.id === routeValue(route.query.endpointId),
))
const aiGeneratedCases = computed(() => {
  const ids = new Set(cases.aiJob?.batches.flatMap(batch => batch.generated_draft_ids) || [])
  return [...ids].map(id => cases.versions[id]).filter((item): item is CaseVersion => Boolean(item))
})
const workspaceRestoreClient = new ApiClient(8_000)

watch(() => context.environmentRevisionId, revisionId => {
  const client = workspaceRestoring.value ? workspaceRestoreClient : apiClient
  void context.loadEnvironmentVariableNames(revisionId, client)
})

onMounted(() => {
  void restoreWorkspace()
})

async function restoreWorkspace(): Promise<void> {
  workspaceRestoring.value = true
  workspaceRestoreFailed.value = false
  context.error = ''
  tasks.error = ''
  assets.error = ''
  localError.value = ''
  try {
    await Promise.all([
      context.loadSavedContext(workspaceRestoreClient),
      context.loadOptions(workspaceRestoreClient),
    ])
    if (context.error) throw new Error(context.error)
    const directNewTask = routeValue(route.query.newTask) === '1'
    const routeContext = restoreExecutionContextFromRoute()
    const routeTaskId = routeValue(route.query.taskId)
    if (routeTaskId && context.projectId) {
      await tasks.list(context.projectId, workspaceRestoreClient)
      if (tasks.error) throw new Error(tasks.error)
    }
    let restoredTask = directNewTask ? null : (routeTaskId ? tasks.select(routeTaskId) : null)
    if (!directNewTask && !restoredTask && context.projectId) {
      restoredTask = await tasks.restore(context.projectId, workspaceRestoreClient)
      if (tasks.error) throw new Error(tasks.error)
    }
    if (directNewTask) {
      tasks.clear()
      taskNameDraft.value = defaultTaskName(true)
    }
    const implicitSourceMismatch = Boolean(
      restoredTask
      && !routeTaskId
      && context.sourceRevisionId
      && restoredTask.project_id === context.projectId
      && restoredTask.source_revision_id !== context.sourceRevisionId,
    )
    if (implicitSourceMismatch && restoredTask) {
      outdatedRestoredTask.value = restoredTask
      tasks.clear()
      restoredTask = null
    } else if (restoredTask && !routeContext) {
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
    if (context.sourceRevisionId) {
      await loadSource(
        context.sourceRevisionId,
        directNewTask ? [] : restoredSelection,
        workspaceRestoreClient,
        true,
      )
    }
    await restoreDeepLink(workspaceRestoreClient, true)
    if (context.projectId && context.sourceRevisionId) {
      void cases.restoreLatestAiJob(context.projectId, context.sourceRevisionId, workspaceRestoreClient)
    }
  } catch (error) {
    workspaceRestoreFailed.value = true
    localError.value = error instanceof Error ? error.message : '工作区恢复失败，请稍后重试'
  } finally {
    workspaceRestoring.value = false
  }
}

watch(() => tasks.task?.name, name => {
  taskNameDraft.value = name || defaultTaskName(routeValue(route.query.newTask) === '1')
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

async function loadSource(
  sourceRevisionId: string,
  restoredSelection: string[] = [],
  client: Pick<ApiClient, 'get'> = apiClient,
  throwOnError = false,
): Promise<void> {
  localError.value = ''
  try {
    await Promise.all([
      assets.load(sourceRevisionId, client),
      cases.loadSavedCases(sourceRevisionId, client),
    ])
    if (assets.error) throw new Error(assets.error)
    activeEndpoint.value = null
    const available = new Set(assets.endpoints.map(item => item.id))
    selectedIds.value = restoredSelection.filter(item => available.has(item))
    endpointTreeTab.value = selectedIds.value.length ? 'selected' : 'all'
    if (selectedIds.value.length === 1) {
      const endpoint = assets.endpoints.find(item => item.id === selectedIds.value[0])
      if (endpoint) await activate(endpoint, client)
    }
  } catch (error) {
    localError.value = error instanceof Error ? error.message : '无法读取已保存接口和用例'
    if (throwOnError) throw new Error(localError.value)
  }
}

function changeProject(projectId: string | null): void {
  context.selectProject(projectId)
  tasks.clear()
  tasks.tasks = []
  outdatedRestoredTask.value = null
  cases.clearDebug()
  cases.clearAiJob()
  debugOpen.value = false
  activeEndpoint.value = null
  selectedIds.value = []
  endpointTreeTab.value = 'all'
  mobilePane.value = 'scope'
  if (projectId) void tasks.list(projectId)
}

async function changeSource(sourceRevisionId: string | null): Promise<void> {
  const recoverableTask = tasks.task || outdatedRestoredTask.value
  context.selectSourceRevision(sourceRevisionId)
  tasks.clear()
  outdatedRestoredTask.value = null
  cases.clearDebug()
  cases.clearAiJob()
  debugOpen.value = false
  mobilePane.value = 'scope'
  if (sourceRevisionId) {
    if (
      recoverableTask
      && recoverableTask.project_id === context.projectId
      && recoverableTask.source_revision_id === sourceRevisionId
    ) {
      tasks.upsertTask(recoverableTask)
      await selectTask(recoverableTask.id)
      return
    }
    if (recoverableTask && recoverableTask.project_id === context.projectId) {
      outdatedRestoredTask.value = recoverableTask
    }
    await loadSource(sourceRevisionId)
    if (context.projectId) await cases.restoreLatestAiJob(context.projectId, sourceRevisionId)
  }
  else {
    assets.endpoints = []
    activeEndpoint.value = null
  }
}

async function restoreOutdatedTask(): Promise<void> {
  if (!outdatedRestoredTask.value) return
  await selectTask(outdatedRestoredTask.value.id)
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
  outdatedRestoredTask.value = null
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
  await cases.restoreLatestAiJob(task.project_id, task.source_revision_id)
  const endpoint = assets.endpoints.find(item => task.selected_endpoint_ids.includes(item.id))
  activeEndpoint.value = endpoint || null
}

async function activate(
  endpoint: ApiEndpoint,
  client: Pick<ApiClient, 'get'> = apiClient,
): Promise<void> {
  localError.value = ''
  const detailed = await assets.ensureEndpointDetail(endpoint.id, client)
  if (!detailed) {
    localError.value = assets.error || '接口详情读取失败，请重试'
    return
  }
  activeEndpoint.value = detailed
  cases.draftFor(detailed)
  mobilePane.value = 'editor'
}

async function openAiGenerated(version: CaseVersion): Promise<void> {
  const endpoint = assets.endpoints.find(item => item.id === version.endpoint_id)
  if (!endpoint) {
    localError.value = '生成用例对应的接口不在当前接口版本中'
    return
  }
  await activate(endpoint)
  if (!activeEndpoint.value) return
  cases.setActiveVersion(version.endpoint_id, version.id)
}

function manageAiGenerated(): void {
  void router.push({ name: 'cases', query: {
    projectId: context.projectId || '',
    sourceRevisionId: context.sourceRevisionId || '',
    environmentRevisionId: context.environmentRevisionId || '',
  } })
}

function openEndpointHistory(endpointId: string, endpointKey?: string): void {
  void router.push({ name: 'runs', query: { endpointId, ...(endpointKey ? { endpointKey } : {}) } })
}

function startNewTask(): void {
  tasks.clear()
  outdatedRestoredTask.value = null
  cases.clearDebug()
  cases.clearAiJob()
  debugOpen.value = false
  localError.value = ''
  selectedIds.value = []
  endpointTreeTab.value = 'all'
  activeEndpoint.value = null
  mobilePane.value = 'scope'
  taskNameDraft.value = defaultTaskName(true)
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

async function restoreDeepLink(
  client: Pick<ApiClient, 'get'> = apiClient,
  throwOnError = false,
): Promise<void> {
  const endpointId = routeValue(route.query.endpointId)
  const caseVersionId = routeValue(route.query.caseVersionId)
  if (!endpointId) return
  const endpoint = assets.endpoints.find(item => item.id === endpointId)
  if (!endpoint) {
    localError.value = '执行记录对应的接口不在当前接口版本中'
    if (throwOnError) throw new Error(localError.value)
    return
  }
  if (caseVersionId) {
    try { await cases.loadVersion(caseVersionId, client) } catch (error) {
      localError.value = error instanceof Error ? error.message : '无法恢复执行记录对应的用例版本'
      if (throwOnError) throw new Error(localError.value)
      return
    }
  }
  await activate(endpoint, client)
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
    if (endpoint) await activate(endpoint)
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
      mobilePane.value = 'editor'
    }
  } catch (error) {
    localError.value = error instanceof Error ? error.message : '基础正向用例生成失败'
  }
}

async function diagnoseValidation(batchId: string, errorIndex: number): Promise<void> {
  await cases.diagnoseAiValidation(batchId, errorIndex)
}

async function submitDebug(): Promise<void> {
  if (!context.projectId || !context.sourceRevisionId || !context.environmentRevisionId || !activeEndpoint.value) return
  if (!confirmApiExecution({
    action: '调试用例',
    environmentName: environmentName.value,
    targetName: activeDraft.value?.name || activeEndpoint.value.summary || activeEndpoint.value.path,
    caseCount: 1,
  })) return
  const endpointId = activeEndpoint.value.id
  localError.value = ''
  let version
  try {
    version = await cases.saveForDebug(endpointId, context.environmentRevisionId)
  } catch (error) {
    localError.value = error instanceof Error ? error.message : '当前草稿保存或校验失败'
    return
  }
  let taskId: string | undefined
  if (editingSavedCaseFromLink.value) {
    taskId = tasks.task?.selected_endpoint_ids.includes(endpointId) ? tasks.task.id : undefined
  } else {
    if (!selectedIds.value.includes(endpointId)) selectedIds.value = [...selectedIds.value, endpointId]
    const task = await saveCurrentTask()
    if (!task) {
      localError.value = `草稿已保存，但调试未开始：${localError.value || tasks.error || '测试任务保存失败'}`
      return
    }
    taskId = task.id
  }
  cases.debugExecution = null
  debugOpen.value = true
  localError.value = ''
  try {
    await cases.debug({ projectId: context.projectId, sourceRevisionId: context.sourceRevisionId, environmentRevisionId: context.environmentRevisionId, caseVersionId: version.id, taskId })
    if (taskId) await tasks.restore(context.projectId)
  } catch (error) {
    cases.debugError = error instanceof Error ? error.message : '调试任务创建失败'
  }
}

async function editDebugStep(target: { stage: 'setup' | 'main' | 'cleanup'; index: number }): Promise<void> {
  debugOpen.value = false
  await nextTick()
  await caseEditor.value?.editStep(target)
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
  if (!confirmApiExecution({
    action: '执行任务',
    environmentName: environmentName.value,
    targetName: tasks.task?.name || taskNameDraft.value.trim() || defaultTaskName(),
    caseCount: taskMatchesSelection.value ? tasks.task?.runnable_baseline_count : undefined,
  })) return
  if (!taskMatchesSelection.value && !await saveCurrentTask()) return
  try {
    const execution = await tasks.runCurrent(context.environmentRevisionId)
    await router.push({ name: 'runs', query: { executionId: execution.id } })
  } catch (error) {
    localError.value = error instanceof Error ? error.message : '测试任务执行失败'
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

function defaultTaskName(newTask = false): string {
  const projectName = context.projects.find(item => item.id === context.projectId)?.name || 'API'
  return newTask ? `${projectName}新建任务` : `${projectName}接口测试`
}

function sourceRevisionLabel(revisionId: string | null): string {
  if (!revisionId) return '未选择接口版本'
  const source = context.sourceRevisions.find(item => item.id === revisionId)
  if (!source) return '任务保存接口版本'
  if (!source.revision_number) return source.name || '任务保存接口版本'
  return `${source.name} · v${source.revision_number}`
}

</script>

<template>
  <section class="workspace workbench-page">
    <header class="page-toolbar"><div><p class="eyebrow">接口测试</p><h1>接口测试工作台</h1><p class="page-subtitle">选接口，AI 设计，保存草稿后直接调试。</p></div><button class="icon-command" type="button" title="重新读取已保存接口" :disabled="!context.sourceRevisionId || assets.state === 'loading'" @click="context.sourceRevisionId && assets.load(context.sourceRevisionId)"><RefreshCw :size="18" /></button></header>
    <div v-if="workspaceRestoring" class="state-message workspace-restoring" data-testid="workspace-restoring">
      <RefreshCw class="spinning" :size="18" />
      <div><strong>正在恢复上次工作区</strong><small>正在读取任务、接口版本和执行环境…</small></div>
    </div>
    <section v-else-if="workspaceRestoreFailed" class="workspace-restore-error" data-testid="workspace-restore-error" role="alert">
      <AlertTriangle :size="20" />
      <div>
        <strong>工作区恢复失败</strong>
        <p>{{ localError }}</p>
        <small>已停止继续读取任务和用例，避免重复等待。服务恢复后可直接重试。</small>
      </div>
      <button class="secondary-command" data-testid="workspace-restore-retry" type="button" @click="restoreWorkspace">
        <RefreshCw :size="16" />重试恢复
      </button>
    </section>
    <template v-else>
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
    <section v-if="outdatedRestoredTask" data-testid="source-version-mismatch" class="source-version-mismatch" role="status">
      <AlertTriangle :size="18" />
      <div>
        <strong>当前接口版本未包含最近任务</strong>
        <p>当前为{{ currentSourceLabel }}；最近任务“{{ outdatedRestoredTask.name }}”属于{{ outdatedTaskSourceLabel }}。任务和用例仍然保留，可直接恢复或到任务管理选择。</p>
      </div>
      <div class="source-version-mismatch-actions">
        <button data-testid="restore-version-task" class="secondary-command" type="button" @click="restoreOutdatedTask">恢复最近任务</button>
        <RouterLink class="secondary-command" :to="{ name: 'tasks' }">查看历史任务</RouterLink>
      </div>
    </section>
    <TaskStatusStrip
      v-model:task-name-draft="taskNameDraft"
      :task="tasks.task"
      :selected-count="selectedIds.length"
      :scope-matches-task="taskMatchesSelection"
      :environment-name="environmentName"
      :saving="tasks.saving"
      :running="tasks.running"
      @rename-task="renameCurrentTask"
      @new="startNewTask"
      @save="saveCurrentTask"
      @run="runCurrentTask"
    />
    <section v-if="editingSavedCaseFromLink" data-testid="standalone-case-edit-note" class="standalone-case-edit-note" role="status">
      <PencilLine :size="18" />
      <div>
        <strong>正在单独编辑已保存用例</strong>
        <p>保存并调试不会更改当前任务的接口范围。需要调整任务时，请在左侧勾选接口后使用上方的任务保存操作。</p>
      </div>
    </section>
    <p v-if="context.error || tasks.error || localError" class="inline-error">{{ context.error || tasks.error || localError }}</p>
    <div class="workbench-shell workbench-shell-focused">
      <nav class="mobile-workbench-tabs" role="tablist" aria-label="移动工作台视图">
        <button id="mobile-workbench-tab-scope" data-testid="mobile-workbench-scope" type="button" role="tab" aria-controls="mobile-workbench-panel-scope" :tabindex="mobilePane === 'scope' ? 0 : -1" :aria-selected="mobilePane === 'scope'" :class="{ active: mobilePane === 'scope' }" @click="mobilePane = 'scope'"><ListTree :size="15" />接口</button>
        <button id="mobile-workbench-tab-editor" data-testid="mobile-workbench-editor" type="button" role="tab" aria-controls="mobile-workbench-panel-editor" :tabindex="mobilePane === 'editor' ? 0 : -1" :aria-selected="mobilePane === 'editor'" :class="{ active: mobilePane === 'editor' }" @click="mobilePane = 'editor'"><PencilLine :size="15" />用例编辑</button>
        <button id="mobile-workbench-tab-ai" data-testid="mobile-workbench-ai" type="button" role="tab" aria-controls="mobile-workbench-panel-ai" :tabindex="mobilePane === 'ai' ? 0 : -1" :aria-selected="mobilePane === 'ai'" :class="{ active: mobilePane === 'ai' }" @click="mobilePane = 'ai'"><Sparkles :size="15" />AI 助手</button>
      </nav>
      <div class="design-workspace">
        <EndpointTree id="mobile-workbench-panel-scope" role="tabpanel" aria-labelledby="mobile-workbench-tab-scope" :class="['mobile-workbench-pane', { 'mobile-pane-active': mobilePane === 'scope' }]" :endpoints="assets.endpoints" :selected-ids="selectedIds" :initial-tab="endpointTreeTab" :state="context.sourceRevisionId ? assets.state : 'empty'" :error="assets.error" @selection-change="selectedIds = $event" @activate="activate" />
        <main id="mobile-workbench-panel-editor" role="tabpanel" aria-labelledby="mobile-workbench-tab-editor" :class="['design-center', 'mobile-workbench-pane', { 'mobile-pane-active': mobilePane === 'editor' }]">
          <EndpointDetail :endpoint="activeEndpoint" @open-history="openEndpointHistory" />
          <CaseEditor v-if="activeDraft" ref="caseEditor" :model-value="activeDraft" :dependency-options="dependencyOptions" :endpoint-options="assets.endpoints" :environment-variable-names="context.environmentVariableNames" :environment-revision-id="context.environmentRevisionId || ''" :environment-name="environmentName" :saving="cases.saving" :debugging="debugRunning" :saved-message="cases.savedMessage" :operation-error="localError" :validation-errors="cases.validationErrors" :validation-warnings="cases.validationWarnings" @update:model-value="updateDraft" @save="saveDraft" @debug="submitDebug" />
          <div v-else class="state-message center-empty">选择接口后，可手工编辑或让 AI 生成测试用例。</div>
        </main>
        <AiAssistant id="mobile-workbench-panel-ai" role="tabpanel" aria-labelledby="mobile-workbench-tab-ai" :class="['mobile-workbench-pane', { 'mobile-pane-active': mobilePane === 'ai' }]" :selected-count="selectedIds.length" :job="cases.aiJob" :generated-cases="aiGeneratedCases" :error="cases.aiError" :polling="cases.aiPolling" :can-resume="cases.aiCanResume" :basic-generating="cases.basicGenerating" :diagnosing-batch-id="cases.aiDiagnosisBatchId" @generate-basic="generateBasicPositive" @generate="generate" @retry="generate" @resume="cases.resumeAiJob()" @diagnose-validation="diagnoseValidation" @open-generated="openAiGenerated" @manage-generated="manageAiGenerated" />
      </div>
    </div>
    </template>
    <DebugDrawer v-if="debugOpen" :open="debugOpen" :case-version-id="activeVersionId" :environment-revision-id="context.environmentRevisionId || ''" :environment-label="environmentLabel" :running="debugRunning" :can-resume="cases.debugCanResume" :result="cases.debugResult" :error="cases.debugError" :baseline-adopting="cases.baselineAdopting" :baseline-message="cases.baselineMessage" :baseline-error="cases.baselineError" @submit="submitDebug" @resume="cases.resumeDebug()" @adopt="adoptBaseline" @edit-step="editDebugStep" @close="debugOpen = false" />
  </section>
</template>
