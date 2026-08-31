<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { ArrowLeft, CircleAlert, CircleCheck, Clock3, ListChecks, Plus, RefreshCw, Sparkles } from 'lucide-vue-next'
import { useRoute, useRouter } from 'vue-router'

import type { ApiEndpoint, CaseDraft, CaseVersion, GeneratedCasePreview } from '../api/contracts'
import CaseEditor from '../components/CaseEditor.vue'
import CaseEndpointPicker from '../components/CaseEndpointPicker.vue'
import CaseListPanel from '../components/CaseListPanel.vue'
import ContextBar from '../components/ContextBar.vue'
import DebugDrawer from '../components/DebugDrawer.vue'
import EndpointDetail from '../components/EndpointDetail.vue'
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
const router = useRouter()
const route = useRoute()

const selectedIds = ref<string[]>([])
const activeEndpoint = ref<ApiEndpoint | null>(null)
const debugOpen = ref(false)
const localError = ref('')
const mobileDetailOpen = ref(false)
const endpointPickerOpen = ref(false)

const activeDraft = computed(() => activeEndpoint.value ? cases.draftFor(activeEndpoint.value) : null)
const activeVersionId = computed(() => activeEndpoint.value ? cases.activeVersionByEndpoint[activeEndpoint.value.id] || '' : '')
const allCaseVersions = computed(() => Object.values(cases.versions))
const caseCountByEndpoint = computed(() => {
  const counts: Record<string, number> = {}
  for (const version of allCaseVersions.value) {
    const endpointId = version.current_endpoint_id || version.endpoint_id
    counts[endpointId] = (counts[endpointId] || 0) + 1
  }
  return counts
})
const dependencyOptions = computed(() => buildCaseDependencyOptions(
  allCaseVersions.value,
  assets.endpoints,
  activeVersionId.value,
))
const debugRunning = computed(() => cases.debugPolling)
const selectedEnvironment = computed(() => context.environmentRevisions.find(item => item.id === context.environmentRevisionId))
const environmentName = computed(() => selectedEnvironment.value?.name || '未选择环境')
const environmentLabel = computed(() => selectedEnvironment.value
  ? `${selectedEnvironment.value.name} · v${selectedEnvironment.value.revision}`
  : context.environmentRevisionId ? '任务保存环境 · 已保存任务引用' : '未选择环境')
const activeTaskName = computed(() => tasks.task?.name || '未绑定任务')
const aiGeneratedVersionIds = computed(() => Array.from(new Set(
  cases.aiJob?.batches.flatMap(batch => batch.generated_draft_ids) || [],
)))
const aiCompletedBatchCount = computed(() => cases.aiJob?.batches.filter(batch => batch.state === 'completed').length || 0)
const aiJobStateLabel = computed(() => {
  if (!cases.aiJob) return ''
  return {
    queued: '排队中',
    running: '生成中',
    completed: '生成完成',
    partial: '部分生成完成',
    failed: '生成失败',
    failed_gateway: 'AI 服务调用失败',
    failed_validation: '生成结果校验失败',
  }[cases.aiJob.state]
})
const aiJobFailed = computed(() => Boolean(cases.aiJob && ['failed', 'failed_gateway', 'failed_validation'].includes(cases.aiJob.state)))
const aiJobGuidance = computed(() => {
  const state = cases.aiJob?.state
  if (state === 'completed') return '结果已保存，可在用例列表继续调试；离开页面后也不会丢失。'
  if (state === 'partial') return '已保存成功生成的用例；可先调试现有结果，再重新生成失败批次。'
  if (state === 'queued' || state === 'running') return '任务在后台继续生成，可以离开页面，稍后回到用例管理查看结果。'
  if (aiJobFailed.value && aiGeneratedVersionIds.value.length) return '已保存成功生成的部分结果，可查看后决定是否重新生成。'
  if (aiJobFailed.value) return '未生成可用结果，请根据下方原因修正后重新生成。'
  return ''
})

function openEndpointHistory(endpointId: string, endpointKey?: string): void {
  void router.push({ name: 'runs', query: { endpointId, ...(endpointKey ? { endpointKey } : {}) } })
}

function openCaseDebugHistory(version: CaseVersion): void {
  const executionId = version.lifecycle?.debug_execution_id
  if (executionId) void router.push({ name: 'runs', query: { executionId } })
}

function openCaseBaseline(version: CaseVersion): void {
  if (!version.lifecycle?.baseline_id) return
  void router.push({ name: 'baselines', query: { search: version.name } })
}

onMounted(async () => {
  await Promise.all([context.loadSavedContext(), context.loadOptions()])
  restoreContextFromRoute()
  if (context.projectId) {
    await tasks.list(context.projectId)
    const restored = await tasks.restore(context.projectId)
    if (restored?.source_revision_id === context.sourceRevisionId) selectedIds.value = [...restored.selected_endpoint_ids]
  }
  if (context.sourceRevisionId) await loadSource(context.sourceRevisionId)
  if (context.projectId && context.sourceRevisionId) {
    await cases.restoreLatestAiJob(context.projectId, context.sourceRevisionId)
  }
})

function restoreContextFromRoute(): boolean {
  const projectId = routeValue(route.query.projectId)
  const sourceRevisionId = routeValue(route.query.sourceRevisionId)
  const requestedEnvironmentId = routeValue(route.query.environmentRevisionId)
  const source = context.sourceRevisions.find(item => item.id === sourceRevisionId && item.project_id === projectId)
  if (!projectId || !source) return false
  const savedEnvironmentId = context.environmentRevisions.some(item => (
    item.id === context.environmentRevisionId && item.project_id === projectId
  )) ? context.environmentRevisionId : null
  const environmentRevisionId = context.environmentRevisions.some(item => (
    item.id === requestedEnvironmentId && item.project_id === projectId
  )) ? requestedEnvironmentId : savedEnvironmentId
  context.restoreExecutionContext({ project_id: projectId, source_revision_id: source.id, environment_revision_id: environmentRevisionId })
  return true
}

function routeValue(value: unknown): string {
  return typeof value === 'string' ? value : ''
}

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
  if (sourceRevisionId && context.projectId) await cases.restoreLatestAiJob(context.projectId, sourceRevisionId)
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
  const endpointId = version.current_endpoint_id || version.endpoint_id
  const endpoint = assets.endpoints.find(item => item.id === endpointId)
  if (!endpoint) {
    localError.value = '该用例对应的接口不在当前接口版本中'
    return
  }
  activeEndpoint.value = endpoint
  mobileDetailOpen.value = true
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
  mobileDetailOpen.value = true
  cases.setDraftFromGeneratedPreview(preview.id)
  debugOpen.value = false
}

async function createManualCase(endpoint: ApiEndpoint): Promise<void> {
  localError.value = ''
  const detailed = await assets.ensureEndpointDetail(endpoint.id)
  if (!detailed) {
    localError.value = assets.error || '接口详情读取失败，请重试'
    return
  }
  activeEndpoint.value = detailed
  cases.startManualDraft(detailed)
  endpointPickerOpen.value = false
  mobileDetailOpen.value = true
}

async function generateBasicForEndpoint(endpoint: ApiEndpoint): Promise<void> {
  if (!context.environmentRevisionId) {
    localError.value = '请先选择执行环境'
    return
  }
  if (!selectedIds.value.includes(endpoint.id)) selectedIds.value = [...selectedIds.value, endpoint.id]
  const task = await saveCurrentTask()
  if (!task) return
  localError.value = ''
  try {
    const previews = await cases.previewBasicPositive([endpoint.id], context.environmentRevisionId, task.id)
    if (previews[0]) editGeneratedPreview(previews[0])
    endpointPickerOpen.value = false
  } catch (error) {
    localError.value = error instanceof Error ? error.message : '基础正向候选生成失败'
  }
}

async function generateAiForEndpoint(endpoint: ApiEndpoint): Promise<void> {
  if (!context.environmentRevisionId) {
    localError.value = '请先选择执行环境'
    return
  }
  if (!selectedIds.value.includes(endpoint.id)) selectedIds.value = [...selectedIds.value, endpoint.id]
  const task = await saveCurrentTask()
  if (!task) return
  await cases.generate(
    [endpoint.id],
    context.environmentRevisionId,
    '覆盖正常流程、参数边界、业务失败和接口契约',
    task.id,
  )
  if (cases.aiError) return
  const generatedId = cases.aiJob?.batches.flatMap(item => item.generated_draft_ids)[0]
  const version = generatedId ? cases.versions[generatedId] : null
  if (version) editCaseVersion(version)
  endpointPickerOpen.value = false
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
    const endpointId = version.current_endpoint_id || version.endpoint_id
    await cases.archiveCase(endpointId, version.id)
    if (activeEndpoint.value?.id === endpointId && !cases.versionIdsByEndpoint[endpointId]?.length) {
      activeEndpoint.value = null
    }
    if (context.projectId) await tasks.restore(context.projectId)
  } catch (error) {
    localError.value = error instanceof Error ? error.message : '用例删除失败'
  }
}

async function updateCaseGroup(version: CaseVersion, groupName: string): Promise<void> {
  localError.value = ''
  try {
    await cases.updateVersionGroup(version.id, groupName)
  } catch (error) {
    localError.value = error instanceof Error ? error.message : '用例分组保存失败'
  }
}

async function updateCaseGroups(versionIds: string[], groupName: string): Promise<void> {
  localError.value = ''
  try {
    await cases.updateVersionGroups(versionIds, groupName)
  } catch (error) {
    localError.value = error instanceof Error ? error.message : '用例批量分组保存失败'
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
  if (!confirmApiExecution({ action: '执行用例', environmentName: environmentName.value, targetName: version.name, caseCount: 1 })) return
  editCaseVersion(version)
  const endpointId = version.current_endpoint_id || version.endpoint_id
  if (!selectedIds.value.includes(endpointId)) selectedIds.value = [...selectedIds.value, endpointId]
  let prepared
  try {
    prepared = await cases.saveForDebug(endpointId, context.environmentRevisionId)
  } catch (error) {
    localError.value = error instanceof Error ? error.message : '当前用例保存或校验失败'
    return
  }
  const task = await saveCurrentTask()
  if (!task) {
    localError.value = `草稿已保存，但调试未开始：${localError.value || tasks.error || '测试任务保存失败'}`
    return
  }
  cases.debugExecution = null
  debugOpen.value = true
  localError.value = ''
  try {
    await cases.debug({
      projectId: context.projectId,
      sourceRevisionId: context.sourceRevisionId,
      environmentRevisionId: context.environmentRevisionId,
      caseVersionId: prepared.id,
      taskId: task.id,
    })
    await tasks.restore(context.projectId)
  } catch (error) {
    cases.debugError = error instanceof Error ? error.message : '用例执行失败'
  }
}

async function submitDebug(): Promise<void> {
  if (!context.projectId || !context.sourceRevisionId || !context.environmentRevisionId || !activeEndpoint.value) return
  if (!confirmApiExecution({
    action: '调试用例',
    environmentName: environmentName.value,
    targetName: activeDraft.value?.name || activeEndpoint.value.summary || activeEndpoint.value.path,
    caseCount: 1,
  })) return
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
  if (!task) {
    localError.value = `草稿已保存，但调试未开始：${localError.value || tasks.error || '测试任务保存失败'}`
    return
  }
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
    const activeCaseId = cases.versions[input.caseVersionId]?.case_id
    try {
      await Promise.all([
        tasks.restore(context.projectId),
        context.sourceRevisionId ? cases.loadSavedCases(context.sourceRevisionId) : Promise.resolve(),
      ])
      const refreshed = activeCaseId
        ? Object.values(cases.versions).find(version => version.case_id === activeCaseId)
        : null
      if (refreshed) editCaseVersion(refreshed)
    } catch (error) {
      localError.value = error instanceof Error ? `基线已采纳，但任务状态刷新失败：${error.message}` : '基线已采纳，但任务状态刷新失败'
    }
  }
}

function openAiGenerationResults(): void {
  const version = aiGeneratedVersionIds.value
    .map(versionId => cases.versions[versionId])
    .find(Boolean)
  if (!version) {
    localError.value = '生成记录已恢复，但当前接口版本中没有可打开的用例'
    return
  }
  editCaseVersion(version)
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
        <p class="eyebrow">用例管理</p>
        <h1>用例管理</h1>
        <p class="page-subtitle">这里保存测试用例及其版本。调试通过后需采纳为基线，才能加入任务回归；“已调试”不代表通过，“已基线”不代表本轮已执行。</p>
      </div>
      <div class="page-toolbar-actions">
        <button data-testid="open-case-endpoint-picker" class="primary-command" type="button" :disabled="!context.sourceRevisionId" @click="endpointPickerOpen = true"><Plus :size="16" />从接口创建用例</button>
        <button class="icon-command" type="button" title="重新读取用例" :disabled="!context.sourceRevisionId || assets.state === 'loading'" @click="refreshCases"><RefreshCw :size="18" /></button>
      </div>
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
    <CaseEndpointPicker
      v-if="endpointPickerOpen"
      :endpoints="assets.endpoints"
      :case-count-by-endpoint="caseCountByEndpoint"
      :busy="cases.saving || cases.basicGenerating || cases.aiPolling"
      @close="endpointPickerOpen = false"
      @create-manual="createManualCase"
      @generate-basic="generateBasicForEndpoint"
      @generate-ai="generateAiForEndpoint"
    />
    <section
      v-if="cases.aiJob"
      :class="['case-generation-status', { failed: aiJobFailed }]"
      data-testid="case-generation-status"
      aria-live="polite"
    >
      <CircleAlert v-if="aiJobFailed" :size="19" />
      <CircleCheck v-else-if="cases.aiJob.state === 'completed'" :size="19" />
      <Clock3 v-else :size="19" />
      <div>
        <strong>{{ aiJobStateLabel }}</strong>
        <p>
          {{ aiCompletedBatchCount }}/{{ cases.aiJob.batches.length }} 批已完成
          <span>·</span>
          {{ aiGeneratedVersionIds.length }} 条用例
        </p>
        <small v-if="aiJobGuidance" class="case-generation-guidance">{{ aiJobGuidance }}</small>
        <small v-if="cases.aiError">{{ cases.aiError }}</small>
      </div>
      <div class="case-generation-actions">
        <button
          v-if="aiGeneratedVersionIds.length"
          data-testid="case-generation-results"
          class="text-command"
          type="button"
          @click="openAiGenerationResults"
        >
          <Sparkles :size="14" />查看生成结果
        </button>
        <button
          v-if="cases.aiCanResume"
          data-testid="case-generation-resume"
          class="secondary-command"
          type="button"
          :disabled="cases.aiPolling"
          @click="cases.resumeAiJob()"
        >继续查看进度</button>
        <button v-else-if="aiJobFailed" class="secondary-command" type="button" @click="endpointPickerOpen = true">重新选择接口</button>
      </div>
    </section>
    <p v-if="context.error || tasks.error || localError" class="inline-error">{{ context.error || tasks.error || localError }}</p>
    <div :class="['management-shell', 'case-management-shell', { 'mobile-detail-open': mobileDetailOpen }]" data-testid="case-management-shell">
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
        @update-version-group="updateCaseGroup"
        @update-version-groups="updateCaseGroups"
        @open-endpoints="endpointPickerOpen = true"
        @open-debug-history="openCaseDebugHistory"
        @open-baseline="openCaseBaseline"
      />
      <main class="management-detail">
        <header class="management-detail-head">
          <button data-testid="management-back-to-list" class="management-back-to-list" type="button" @click="mobileDetailOpen = false"><ArrowLeft :size="16" />返回用例列表</button>
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
          <EndpointDetail :endpoint="activeEndpoint" @open-history="openEndpointHistory" />
          <CaseEditor
            v-if="activeDraft"
            :model-value="activeDraft"
            :dependency-options="dependencyOptions"
            :endpoint-options="assets.endpoints"
            :environment-variable-names="context.environmentVariableNames"
            :environment-revision-id="context.environmentRevisionId || ''"
            :environment-name="environmentName"
            :saving="cases.saving"
            :debugging="debugRunning"
            :saved-message="cases.savedMessage"
            :operation-error="localError"
            :validation-errors="cases.validationErrors"
            :validation-warnings="cases.validationWarnings"
            @update:model-value="updateDraft"
            @save="saveDraft"
            @debug="submitDebug"
          />
          <div v-else class="state-message center-empty">从左侧选择一个用例后，可查看接口信息并编辑测试数据。</div>
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
