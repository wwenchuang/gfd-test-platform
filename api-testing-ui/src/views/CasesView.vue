<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
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
import { captureApiExecutionScope, confirmApiExecution, matchesApiExecutionScope, type ApiExecutionScope } from '../utils/executionConfirmation'
import { endpointGroupName } from '../utils/endpointGroups'
import { inferTestApplicationBusiness } from '../utils/testApplications'
import { useRawEditorGuard } from '../utils/rawEditorGuard'

const context = useContextStore()
const assets = useAssetsStore()
const cases = useCasesStore()
const tasks = useTasksStore()
const router = useRouter()
const route = useRoute()

const selectedIds = ref<string[]>([])
const activeEndpoint = ref<ApiEndpoint | null>(null)
const caseEditor = ref<{ hasUnappliedRaw(): boolean; discardUnappliedRaw(): void } | null>(null)
const rawEditorGuard = useRawEditorGuard(caseEditor)
function requireAppliedRaw(): boolean {
  if (!rawEditorGuard.hasRaw()) return true
  localError.value = '原始 JSON 有未应用的内容，请先修正或放弃后再操作。'
  return false
}
const debugOpen = ref(false)
const executionStarting = ref(false)
const localError = ref('')
const sourceLoadError = ref('')
let sourceLoadGeneration = 0
let manualDetailGeneration = 0
let taskSelectionGeneration = 0
watch(() => tasks.task?.id, () => { taskSelectionGeneration += 1 }, { flush: 'sync' })
let pageActive = true
onUnmounted(() => { pageActive = false; taskSelectionGeneration += 1 })
const isExecutionScopeCurrent = (scope: ApiExecutionScope) => pageActive && matchesApiExecutionScope(context, scope)
const mobileDetailOpen = ref(false)
const endpointPickerOpen = ref(false)
const taskReadNote = ref('')

const activeDraft = computed(() => activeEndpoint.value ? cases.draftFor(activeEndpoint.value) : null)
const activeVersionId = computed(() => activeEndpoint.value ? cases.activeVersionByEndpoint[activeEndpoint.value.id] || '' : '')
const currentSourceReady = computed(() => Boolean(
  context.sourceRevisionId && cases.draftScopeReady && cases.draftSourceRevisionId === context.sourceRevisionId,
))
const allCaseVersions = computed(() => currentSourceReady.value ? Object.values(cases.versions) : [])
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

async function loadSource(sourceRevisionId: string, preserveDrafts = false): Promise<void> {
  const generation = ++sourceLoadGeneration
  try {
    const caseLoad = preserveDrafts
      ? cases.loadSavedCases(sourceRevisionId, undefined, { preserveDrafts: true })
      : cases.loadSavedCases(sourceRevisionId)
    const results = await Promise.all([assets.load(sourceRevisionId), caseLoad])
    if (generation !== sourceLoadGeneration || sourceRevisionId !== context.sourceRevisionId) return
    if (results.includes(false)) {
      activeEndpoint.value = null
      endpointPickerOpen.value = false
      sourceLoadError.value = '读取期间数据发生变化，请重新读取用例。'
      return
    }
    if (assets.error) throw new Error(assets.error)
    sourceLoadError.value = ''
    if (!preserveDrafts) activeEndpoint.value = null
    const available = new Set(assets.endpoints.map(item => item.id))
    selectedIds.value = selectedIds.value.filter(item => available.has(item))
  } catch (error) {
    if (generation !== sourceLoadGeneration || sourceRevisionId !== context.sourceRevisionId) return
    activeEndpoint.value = null
    endpointPickerOpen.value = false
    sourceLoadError.value = error instanceof Error ? error.message : '无法读取接口和用例'
  }
}

function changeProject(projectId: string | null): void {
  if (projectId === context.projectId || !rawEditorGuard.confirmDiscardRaw()) return
  manualDetailGeneration += 1
  sourceLoadGeneration += 1
  assets.invalidateLoad()
  cases.invalidateLoad()
  cases.hideDraftScope()
  sourceLoadError.value = ''
  localError.value = ''
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
  if (sourceRevisionId === context.sourceRevisionId || !rawEditorGuard.confirmDiscardRaw()) return
  manualDetailGeneration += 1
  sourceLoadGeneration += 1
  assets.invalidateLoad()
  assets.endpoints = []
  cases.invalidateLoad()
  cases.hideDraftScope()
  sourceLoadError.value = ''
  localError.value = ''
  context.selectSourceRevision(sourceRevisionId)
  tasks.clear()
  cases.clearDebug()
  cases.clearAiJob()
  debugOpen.value = false
  activeEndpoint.value = null
  selectedIds.value = []
  if (sourceRevisionId) {
    const projectId = context.projectId
    const selectionGeneration = context.selectionGeneration
    const loading = loadSource(sourceRevisionId)
    const generation = sourceLoadGeneration
    await loading
    if (generation !== sourceLoadGeneration || context.selectionGeneration !== selectionGeneration || context.projectId !== projectId
        || context.sourceRevisionId !== sourceRevisionId) return
    if (projectId) await cases.restoreLatestAiJob(projectId, sourceRevisionId)
  }
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

function confirmDraftReplacement(endpointId: string, replacing: boolean, confirmed = false): boolean {
  const rawWillBeLost = Boolean(caseEditor.value?.hasUnappliedRaw?.()
    && (activeEndpoint.value?.id !== endpointId || replacing))
  const dirtyWillBeLost = replacing && Boolean(cases.dirtyDrafts[endpointId])
  if ((rawWillBeLost || dirtyWillBeLost) && !confirmed
      && !window.confirm('存在未保存的修改或未应用的原始 JSON。继续将丢失这些内容，确定继续吗？')) return false
  if (rawWillBeLost) caseEditor.value?.discardUnappliedRaw?.()
  return true
}

function editCaseVersion(version: CaseVersion, replaceConfirmed = false): boolean {
  manualDetailGeneration += 1
  const endpointId = version.current_endpoint_id || version.endpoint_id
  const endpoint = assets.endpoints.find(item => item.id === endpointId)
  if (!endpoint) {
    localError.value = '该用例对应的接口不在当前接口版本中'
    return false
  }
  const replacing = cases.activeVersionByEndpoint[endpoint.id] !== version.id
  if (!confirmDraftReplacement(endpoint.id, replacing, replaceConfirmed)) return false
  activeEndpoint.value = endpoint
  mobileDetailOpen.value = true
  if (replacing) {
    cases.clearDebug()
    debugOpen.value = false
    cases.setActiveVersion(endpoint.id, version.id)
  }
  return true
}

function editGeneratedPreview(preview: GeneratedCasePreview): void {
  manualDetailGeneration += 1
  const endpoint = assets.endpoints.find(item => item.id === preview.endpoint_id)
  if (!endpoint) {
    localError.value = '该候选用例对应的接口不在当前接口版本中'
    return
  }
  if (cases.activeGeneratedPreviewId === preview.id) {
    if (!confirmDraftReplacement(endpoint.id, false)) return
    activeEndpoint.value = endpoint
    mobileDetailOpen.value = true
    return
  }
  if (!confirmDraftReplacement(endpoint.id, true)) return
  activeEndpoint.value = endpoint
  mobileDetailOpen.value = true
  cases.setDraftFromGeneratedPreview(preview.id)
  debugOpen.value = false
}

async function createManualCase(endpoint: ApiEndpoint): Promise<void> {
  if (!currentSourceReady.value) {
    localError.value = '当前来源尚未读取成功，请重新读取后再创建用例'
    return
  }
  const generation = ++manualDetailGeneration
  const sourceGeneration = sourceLoadGeneration
  const assetsGeneration = assets.loadGeneration
  const draftScopeGeneration = cases.draftScopeGeneration
  const projectId = context.projectId
  const sourceRevisionId = context.sourceRevisionId
  localError.value = ''
  const detailed = await assets.ensureEndpointDetail(endpoint.id)
  if (generation !== manualDetailGeneration || sourceGeneration !== sourceLoadGeneration
      || assetsGeneration !== assets.loadGeneration || draftScopeGeneration !== cases.draftScopeGeneration
      || projectId !== context.projectId || sourceRevisionId !== context.sourceRevisionId || !currentSourceReady.value) return
  if (!detailed) {
    localError.value = assets.detailError || '接口详情读取失败，请重试'
    return
  }
  if (!confirmDraftReplacement(detailed.id, true)) return
  activeEndpoint.value = detailed
  const draft = cases.startManualDraft(detailed)
  const ownership = inferTestApplicationBusiness([endpointGroupName(detailed), ...detailed.tags])
  if (ownership) {
    draft.app_package = ownership.appPackage
    draft.app_name = ownership.appName
    draft.business = ownership.business
  }
  endpointPickerOpen.value = false
  mobileDetailOpen.value = true
}

async function generateBasicForEndpoint(endpoint: ApiEndpoint): Promise<void> {
  if (!currentSourceReady.value) return
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
    if (previews[0] && !rawEditorGuard.hasRaw() && !cases.dirtyDrafts[previews[0].endpoint_id]
        && !(activeEndpoint.value && cases.dirtyDrafts[activeEndpoint.value.id])) editGeneratedPreview(previews[0])
    endpointPickerOpen.value = false
  } catch (error) {
    localError.value = error instanceof Error ? error.message : '基础正向候选生成失败'
  }
}

async function generateAiForEndpoint(endpoint: ApiEndpoint): Promise<void> {
  if (!currentSourceReady.value) return
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
  if (version && !rawEditorGuard.hasRaw() && !cases.dirtyDrafts[version.current_endpoint_id || version.endpoint_id]
      && !(activeEndpoint.value && cases.dirtyDrafts[activeEndpoint.value.id])) editCaseVersion(version)
  endpointPickerOpen.value = false
}

function openEndpointPicker(): void {
  if (currentSourceReady.value) endpointPickerOpen.value = true
}

function updateDraft(draft: CaseDraft): void {
  if (!activeEndpoint.value) return
  cases.updateDraft(activeEndpoint.value.id, draft)
  cases.clearDebug()
  debugOpen.value = false
}

async function saveDraft() {
  if (!requireAppliedRaw()) return
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
  if (!requireAppliedRaw()) return
  const selectionGeneration = context.selectionGeneration
  const detailGeneration = manualDetailGeneration
  localError.value = ''
  try {
    const draft = cases.activeGeneratedPreviewId === preview.id ? activeDraft.value || preview.case : preview.case
    const version = await cases.saveGeneratedPreview(preview.id, draft)
    if (selectionGeneration !== context.selectionGeneration || detailGeneration !== manualDetailGeneration) return
    if (mayOpenSavedVersion(version)) editCaseVersion(version)
    if (context.projectId) await tasks.restore(context.projectId)
  } catch (error) {
    localError.value = error instanceof Error ? error.message : '候选用例保存失败'
  }
}

async function saveAllGeneratedPreviews(): Promise<void> {
  if (!requireAppliedRaw()) return
  const selectionGeneration = context.selectionGeneration
  const detailGeneration = manualDetailGeneration
  localError.value = ''
  try {
    const overrides = cases.activeGeneratedPreviewId && activeDraft.value
      ? { [cases.activeGeneratedPreviewId]: activeDraft.value }
      : {}
    const versions = await cases.saveAllGeneratedPreviews(overrides)
    if (selectionGeneration !== context.selectionGeneration || detailGeneration !== manualDetailGeneration) return
    if (versions[0] && mayOpenSavedVersion(versions[0])) editCaseVersion(versions[0])
    if (context.projectId) await tasks.restore(context.projectId)
  } catch (error) {
    localError.value = error instanceof Error ? error.message : '候选用例批量保存失败'
  }
}

function mayOpenSavedVersion(version: CaseVersion): boolean {
  if (rawEditorGuard.hasRaw()) return false
  const endpointId = activeEndpoint.value?.id
  if (!endpointId || !cases.dirtyDrafts[endpointId]) return true
  return endpointId === (version.current_endpoint_id || version.endpoint_id)
    && cases.activeVersionByEndpoint[endpointId] === version.id
}

function discardGeneratedPreview(previewId: string): void {
  const wasActive = cases.activeGeneratedPreviewId === previewId
  if (wasActive && !rawEditorGuard.confirmDiscardRaw()) return
  cases.discardGeneratedPreview(previewId)
  if (wasActive && activeEndpoint.value && !cases.drafts[activeEndpoint.value.id]) activeEndpoint.value = null
}

async function deleteCaseVersion(version: CaseVersion): Promise<void> {
  if (activeEndpoint.value?.id === (version.current_endpoint_id || version.endpoint_id) && !requireAppliedRaw()) return
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

async function saveCurrentTask(confirmedScope?: ApiExecutionScope, confirmedTaskGeneration = taskSelectionGeneration) {
  const scope = confirmedScope || captureApiExecutionScope(context)
  if (!scope) {
    localError.value = '请先选择接口项目、接口版本和执行环境'
    return null
  }
  if (!selectedIds.value.length) {
    localError.value = '请至少选择一个接口'
    return null
  }
  localError.value = ''
  try {
    if (!isExecutionScopeCurrent(scope) || taskSelectionGeneration !== confirmedTaskGeneration) throw new Error('范围已变化或当前任务已变化，任务未保存，请重新确认。')
    const endpointIds = [...selectedIds.value]
    await context.saveContext()
    if (context.error) throw new Error(context.error)
    if (!isExecutionScopeCurrent(scope) || taskSelectionGeneration !== confirmedTaskGeneration) throw new Error('范围已变化或当前任务已变化，任务未保存，请重新确认。')
    const task = await tasks.saveSelection({
      projectId: scope.projectId,
      sourceRevisionId: scope.sourceRevisionId,
      environmentRevisionId: scope.environmentRevisionId,
    }, endpointIds, tasks.task?.name || defaultTaskName(), { isCurrent: () => isExecutionScopeCurrent(scope) && taskSelectionGeneration === confirmedTaskGeneration })
    if (!isExecutionScopeCurrent(scope)
        || (taskSelectionGeneration !== confirmedTaskGeneration
          && !(taskSelectionGeneration === confirmedTaskGeneration + 1 && tasks.task?.id === task.id))) {
      throw new Error('任务已保存，但范围或当前任务已变化，请重新确认。')
    }
    return { ...task, executionSelectionGeneration: taskSelectionGeneration }
  } catch (error) {
    localError.value = error instanceof Error ? error.message : '任务范围保存失败'
    return null
  }
}

async function runCaseVersion(version: CaseVersion): Promise<void> {
  if (executionStarting.value) return
  executionStarting.value = true
  try { await runCaseVersionOnce(version) } finally { executionStarting.value = false }
}

async function runCaseVersionOnce(version: CaseVersion): Promise<void> {
  if (rawEditorGuard.hasRaw()) {
    localError.value = '原始 JSON 有未应用的内容，请先修正或放弃后再调试。'
    return
  }
  const scope = captureApiExecutionScope(context)
  if (!scope) {
    localError.value = '请先选择接口项目、接口版本和执行环境'
    return
  }
  const confirmedTaskGeneration = taskSelectionGeneration
  const endpointId = version.current_endpoint_id || version.endpoint_id
  if (!assets.endpoints.some(endpoint => endpoint.id === endpointId)) {
    localError.value = '该用例对应的接口不在当前接口版本中'
    return
  }
  const targetDraft = cases.drafts[endpointId]
  const currentVersion = Boolean(targetDraft && version.id === cases.activeVersionByEndpoint[endpointId])
  const sourceEffect = currentVersion
    ? '将使用当前编辑内容（包含未保存修改）。'
    : targetDraft ? '将读取所选已保存版本，并替换该接口的未保存编辑。' : '将读取所选已保存版本。'
  if (!confirmApiExecution({
    action: '调试用例',
    environmentName: environmentName.value,
    targetName: currentVersion ? targetDraft.name || version.name : version.name,
    caseCount: 1,
    effect: `${sourceEffect}将保存用例新版本并更新当前任务范围；不会自动替换已采纳的基线。`,
  })) return
  if (!isExecutionScopeCurrent(scope) || taskSelectionGeneration !== confirmedTaskGeneration) {
    localError.value = '范围已变化，调试未开始，请重新确认。'
    return
  }
  if (!editCaseVersion(version, true)) return
  if (!selectedIds.value.includes(endpointId)) selectedIds.value = [...selectedIds.value, endpointId]
  let prepared
  try {
    prepared = await cases.saveForDebug(endpointId, scope.environmentRevisionId)
  } catch (error) {
    localError.value = !isExecutionScopeCurrent(scope)
      ? '范围已变化，保存状态请核对；调试未开始。'
      : error instanceof Error ? error.message : '当前用例保存或校验失败'
    return
  }
  if (!isExecutionScopeCurrent(scope) || taskSelectionGeneration !== confirmedTaskGeneration) {
    localError.value = '草稿已保存，但调试未开始：范围已变化，请重新确认。'
    return
  }
  const savedDraftIdentity = cases.draftIdentities[endpointId] || 0
  const savedPreviewId = cases.activeGeneratedPreviewId
  const savedDraftIsCurrent = () => activeEndpoint.value?.id === endpointId
    && !cases.dirtyDrafts[endpointId] && !cases.missingDraftVersionByEndpoint[endpointId]
    && cases.activeVersionByEndpoint[endpointId] === prepared.id
    && (cases.draftIdentities[endpointId] || 0) === savedDraftIdentity
    && cases.activeGeneratedPreviewId === savedPreviewId
  if (!savedDraftIsCurrent()) {
    localError.value = '草稿已保存，但调试未开始：当前编辑内容或选择已变化，请重新确认。'
    return
  }
  const task = await saveCurrentTask(scope, confirmedTaskGeneration)
  if (!task) {
    localError.value = `草稿已保存，但调试未开始：${localError.value || tasks.error || '测试任务保存失败'}`
    return
  }
  if (!isExecutionScopeCurrent(scope) || taskSelectionGeneration !== task.executionSelectionGeneration) {
    localError.value = '草稿已保存，但调试未开始：范围已变化，请重新确认。'
    return
  }
  if (!savedDraftIsCurrent()) {
    localError.value = '草稿已保存，但调试未开始：当前编辑内容或选择已变化，请重新确认。'
    return
  }
  cases.debugExecution = null
  debugOpen.value = true
  localError.value = ''
  const debugTaskGeneration = task.executionSelectionGeneration
  const debugIsCurrent = () => isExecutionScopeCurrent(scope) && taskSelectionGeneration === debugTaskGeneration
  try {
    await cases.debug({
      projectId: scope.projectId,
      sourceRevisionId: scope.sourceRevisionId,
      environmentRevisionId: scope.environmentRevisionId,
      caseVersionId: prepared.id,
      taskId: task.id,
      isCurrent: debugIsCurrent,
    })
    if (!debugIsCurrent()) {
      localError.value = '调试请求已提交，范围已变化，请前往执行记录核对结果。'
      return
    }
    await tasks.restore(scope.projectId)
  } catch (error) {
    if (!debugIsCurrent()) localError.value = '范围已变化，调试请求状态请在执行记录中核对。'
    else cases.debugError = error instanceof Error ? error.message : '用例执行失败'
  }
}

async function submitDebug(): Promise<void> {
  if (executionStarting.value) return
  executionStarting.value = true
  try { await submitDebugOnce() } finally { executionStarting.value = false }
}

async function submitDebugOnce(): Promise<void> {
  if (!requireAppliedRaw()) return
  const scope = captureApiExecutionScope(context)
  if (!scope || !activeEndpoint.value) return
  const confirmedTaskGeneration = taskSelectionGeneration
  if (!confirmApiExecution({
    action: '调试用例',
    environmentName: environmentName.value,
    targetName: activeDraft.value?.name || activeEndpoint.value.summary || activeEndpoint.value.path,
    caseCount: 1,
    effect: activeVersionId.value
      ? '将保存用例新版本并更新当前任务范围；不会自动替换已采纳的基线。'
      : '将创建并保存新用例，并更新当前任务范围；不会自动采纳基线。',
  })) return
  if (!isExecutionScopeCurrent(scope) || taskSelectionGeneration !== confirmedTaskGeneration) {
    localError.value = '范围已变化，调试未开始，请重新确认。'
    return
  }
  const endpointId = activeEndpoint.value.id
  localError.value = ''
  let version
  try {
    version = await cases.saveForDebug(endpointId, scope.environmentRevisionId)
  } catch (error) {
    localError.value = !isExecutionScopeCurrent(scope)
      ? '范围已变化，保存状态请核对；调试未开始。'
      : error instanceof Error ? error.message : '当前草稿保存或校验失败'
    return
  }
  if (!isExecutionScopeCurrent(scope) || taskSelectionGeneration !== confirmedTaskGeneration) {
    localError.value = '草稿已保存，但调试未开始：范围已变化，请重新确认。'
    return
  }
  const savedDraftIdentity = cases.draftIdentities[endpointId] || 0
  const savedPreviewId = cases.activeGeneratedPreviewId
  const savedDraftIsCurrent = () => activeEndpoint.value?.id === endpointId
    && !cases.dirtyDrafts[endpointId] && !cases.missingDraftVersionByEndpoint[endpointId]
    && cases.activeVersionByEndpoint[endpointId] === version.id
    && (cases.draftIdentities[endpointId] || 0) === savedDraftIdentity
    && cases.activeGeneratedPreviewId === savedPreviewId
  if (!savedDraftIsCurrent()) {
    localError.value = '草稿已保存，但调试未开始：当前编辑内容或选择已变化，请重新确认。'
    return
  }
  if (!selectedIds.value.includes(endpointId)) selectedIds.value = [...selectedIds.value, endpointId]
  const task = await saveCurrentTask(scope, confirmedTaskGeneration)
  if (!task) {
    localError.value = `草稿已保存，但调试未开始：${localError.value || tasks.error || '测试任务保存失败'}`
    return
  }
  if (!isExecutionScopeCurrent(scope) || taskSelectionGeneration !== task.executionSelectionGeneration) {
    localError.value = '草稿已保存，但调试未开始：范围已变化，请重新确认。'
    return
  }
  if (!savedDraftIsCurrent()) {
    localError.value = '草稿已保存，但调试未开始：当前编辑内容或选择已变化，请重新确认。'
    return
  }
  cases.debugExecution = null
  debugOpen.value = true
  localError.value = ''
  const debugTaskGeneration = task.executionSelectionGeneration
  const debugIsCurrent = () => isExecutionScopeCurrent(scope) && taskSelectionGeneration === debugTaskGeneration
  try {
    await cases.debug({
      projectId: scope.projectId,
      sourceRevisionId: scope.sourceRevisionId,
      environmentRevisionId: scope.environmentRevisionId,
      caseVersionId: version.id,
      taskId: task.id,
      isCurrent: debugIsCurrent,
    })
    if (!debugIsCurrent()) {
      localError.value = '调试请求已提交，范围已变化，请前往执行记录核对结果。'
      return
    }
    await tasks.restore(scope.projectId)
  } catch (error) {
    if (!debugIsCurrent()) localError.value = '范围已变化，调试请求状态请在执行记录中核对。'
    else cases.debugError = error instanceof Error ? error.message : '调试任务创建失败'
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
  if (!rawEditorGuard.confirmDiscardRaw()) return
  if (context.sourceRevisionId) await loadSource(context.sourceRevisionId, true)
}

async function retryTaskRead(kind: 'list' | 'restore'): Promise<void> {
  const projectId = context.projectId
  if (!projectId) return
  taskReadNote.value = ''
  if (kind === 'list') await tasks.list(projectId)
  else await tasks.restore(projectId)
  if (context.projectId === projectId && !tasks.readErrors[kind]) {
    taskReadNote.value = kind === 'list' ? '任务列表已重新读取。' : '当前任务已重新读取；当前勾选范围未改变。'
  }
}

async function retryWorkspaceRead(): Promise<void> {
  const hadSelection = Boolean(context.projectId || context.sourceRevisionId || context.environmentRevisionId)
  await context.loadSavedContext(undefined, { preserveSelection: true })
  if (context.readErrors.workspace || hadSelection || !context.projectId || !context.sourceRevisionId) return
  const projectId = context.projectId
  const sourceRevisionId = context.sourceRevisionId
  await tasks.list(projectId)
  if (context.projectId !== projectId || context.sourceRevisionId !== sourceRevisionId) return
  await tasks.restore(projectId)
  if (context.projectId !== projectId || context.sourceRevisionId !== sourceRevisionId) return
  await loadSource(sourceRevisionId)
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
        <button data-testid="open-case-endpoint-picker" class="primary-command" type="button" :disabled="!currentSourceReady" @click="openEndpointPicker"><Plus :size="16" />从接口创建用例</button>
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
      v-if="endpointPickerOpen && currentSourceReady"
      :endpoints="assets.endpoints"
      :case-count-by-endpoint="caseCountByEndpoint"
      :busy="cases.saving || cases.basicGenerating || cases.aiPolling"
      @close="manualDetailGeneration += 1; endpointPickerOpen = false"
      @create-manual="createManualCase"
      @generate-basic="generateBasicForEndpoint"
      @generate-ai="generateAiForEndpoint"
    />
    <section
      v-if="currentSourceReady && cases.aiJob"
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
        <button v-else-if="aiJobFailed" class="secondary-command" type="button" @click="openEndpointPicker">重新选择接口</button>
      </div>
    </section>
    <p v-if="context.readErrors.workspace" class="inline-error" role="alert">工作区：{{ context.readErrors.workspace }}。 <button data-testid="retry-workspace" type="button" :disabled="context.loading" @click="retryWorkspaceRead">重试工作区</button></p>
    <p v-if="context.readErrors.options" class="inline-error" role="alert">项目与环境：{{ context.readErrors.options }}。 <button data-testid="retry-context-options" type="button" :disabled="context.optionsLoading" @click="context.loadOptions()">重试项目与环境</button></p>
    <p v-if="context.saveError" class="inline-error" role="alert">工作区保存：{{ context.saveError }}。请重新保存测试范围。</p>
    <p v-if="context.error && !context.errorSource" class="inline-error" role="alert">项目与环境：{{ context.error }}。重新读取用例不会重试此请求。</p>
    <p v-if="tasks.readErrors.list" class="inline-error" role="alert">任务列表：{{ tasks.readErrors.list }}。重新读取用例不会重试此请求。 <button data-testid="retry-task-list" type="button" :disabled="!context.projectId || tasks.loading" @click="retryTaskRead('list')">重试任务列表</button></p>
    <p v-if="tasks.readErrors.restore" class="inline-error" role="alert">当前任务：{{ tasks.readErrors.restore }}。重新读取用例不会重试此请求。 <button data-testid="retry-task-restore" type="button" :disabled="!context.projectId || tasks.loading" @click="retryTaskRead('restore')">重试当前任务</button></p>
    <p v-if="tasks.operationError" class="inline-error" role="alert">上次任务操作：{{ tasks.operationError }}。 <button type="button" @click="tasks.operationError = ''">关闭提示</button></p>
    <p v-if="tasks.error && !tasks.errorSource" class="inline-error" role="alert">任务信息：{{ tasks.error }}。重新读取用例不会重试此请求。</p>
    <p v-if="taskReadNote" role="status">{{ taskReadNote }}</p>
    <p v-if="sourceLoadError" class="inline-error" role="alert">接口和用例：{{ sourceLoadError }}</p>
    <p v-if="localError" class="inline-error" role="alert">{{ localError }}</p>
    <div :class="['management-shell', 'case-management-shell', { 'mobile-detail-open': mobileDetailOpen }]" data-testid="case-management-shell">
      <CaseListPanel
        :endpoints="assets.endpoints"
        :versions="allCaseVersions"
        :generated-previews="currentSourceReady ? cases.generatedPreviews : []"
        :active-version-id="activeVersionId"
        :active-preview-id="cases.activeGeneratedPreviewId"
        :selected-endpoint-ids="selectedIds"
        :saving="cases.saving || cases.basicGenerating"
        :running="debugRunning || executionStarting"
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
        @open-endpoints="openEndpointPicker"
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
            <button class="secondary-command" type="button" :disabled="!selectedIds.length || tasks.saving" @click="saveCurrentTask()">
              <ListChecks :size="15" />保存任务范围
            </button>
            <button class="secondary-command" type="button" :disabled="!tasks.task" @click="openWorkbenchForTask">编辑任务范围</button>
          </div>
        </header>
        <div class="management-detail-body">
          <EndpointDetail :endpoint="activeEndpoint" @open-history="openEndpointHistory" />
          <CaseEditor
            v-if="activeDraft"
            ref="caseEditor"
            :model-value="activeDraft"
            :draft-identity="`${activeEndpoint?.id || ''}:${activeVersionId}:${cases.activeGeneratedPreviewId || ''}`"
            :editing-version="cases.versions[activeVersionId] || null"
            :dependency-options="dependencyOptions"
            :endpoint-options="assets.endpoints"
            :environment-variable-names="context.environmentVariableNames"
            :environment-revision-id="context.environmentRevisionId || ''"
            :environment-name="environmentName"
            :saving="cases.saving || executionStarting"
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
      :running="debugRunning || executionStarting"
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
