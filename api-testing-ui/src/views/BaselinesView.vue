<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { Edit3, FilePlus2, ListPlus, Play, RefreshCw, ScanSearch, Search, ShieldCheck, Trash2 } from 'lucide-vue-next'
import { useRoute, useRouter } from 'vue-router'

import ContextBar from '../components/ContextBar.vue'
import type { ApiBaselineCase, BaselineAssertionAuditItem, BaselineAssertionAuditStatus } from '../api/contracts'
import { baselineGroup, useBaselinesStore } from '../stores/baselines'
import { useContextStore } from '../stores/context'
import { useExecutionsStore } from '../stores/executions'
import { useTasksStore } from '../stores/tasks'
import { hasExplicitOneTimeMarker } from '../utils/caseClassification'
import { confirmApiExecution } from '../utils/executionConfirmation'
import { applicationBusinessLabel, applicationBusinessSelection } from '../utils/testApplications'

const context = useContextStore()
const baselines = useBaselinesStore()
const executions = useExecutionsStore()
const tasks = useTasksStore()
const router = useRouter()
const route = useRoute()
const search = ref(typeof route.query.search === 'string' ? route.query.search : '')
const group = ref('all')
const baselineType = ref<'all' | 'regular' | 'one-time'>('all')
const methodFilter = ref('all')
const priorityFilter = ref('all')
const originFilter = ref('all')
const auditFilter = ref<'all' | 'needs-review' | BaselineAssertionAuditStatus>('all')
const groupName = ref('')
const moveTargetGroup = ref('')
const localError = ref('')
const localMessage = ref('')
const baselinePage = ref(1)
const BASELINE_PAGE_SIZE = 25

const projectReady = computed(() => Boolean(context.projectId))
const projectName = computed(() => context.projects.find(item => item.id === context.projectId)?.name || '未选择项目')
const selectedSourceName = computed(() => {
  const source = context.sourceRevisions.find(item => item.id === context.sourceRevisionId)
  return source ? `${source.name} · v${source.revision_number}` : '未选择接口版本'
})
const environmentName = computed(() => {
  const environment = context.environmentRevisions.find(item => item.id === context.environmentRevisionId)
  return environment ? `${environment.name} · v${environment.revision}` : '未选择环境'
})
const selectedBaselineSourceRevisionIds = computed(() => {
  const ids = new Set<string>()
  for (const item of baselines.selectedItems) {
    if (item.source_revision_id) ids.add(item.source_revision_id)
  }
  return [...ids]
})
const baselineActionSourceRevisionId = computed(() => {
  if (selectedBaselineSourceRevisionIds.value.length === 1) return selectedBaselineSourceRevisionIds.value[0]
  if (selectedBaselineSourceRevisionIds.value.length === 0) {
    return baselines.items[0]?.source_revision_id || context.sourceRevisionId || ''
  }
  return ''
})
const executionSourceRevisionId = computed(() => {
  return baselineActionSourceRevisionId.value || context.sourceRevisionId || ''
})
const selectedBaselineScopeIssue = computed(() => {
  for (const item of baselines.selectedItems) {
    const selection = baselineSelection(item)
    if (!selection.selectable) return selection.reason
  }
  return ''
})
const selectedAuditEnvironmentIssue = computed(() => {
  if (!context.environmentRevisionId) return ''
  const mismatched = baselines.selectedItems.some(item => {
    const audit = baselines.auditByBaselineId.get(item.id)
    return Boolean(
      audit
      && audit.status !== 'verified'
      && audit.execution.selectable
      && audit.environment_revision_id !== context.environmentRevisionId,
    )
  })
  return mismatched
    ? '所选复核基线的审计证据环境与当前执行环境不一致，请切回原基线环境或重新选择'
    : ''
})
const selectedBaselineActionIssue = computed(() => (
  selectedBaselineScopeIssue.value || selectedAuditEnvironmentIssue.value
))
const baselineActionReady = computed(() => Boolean(
  context.projectId
  && context.environmentRevisionId
  && baselines.selectedIds.length
  && !selectedBaselineActionIssue.value,
))
const selectedSourceById = computed(() => new Map(context.sourceRevisions.map(item => [item.id, item])))
const methodOptions = computed(() => [...new Set(baselines.items.map(item => item.method.toUpperCase()))].sort())
const filteredBaselines = computed(() => {
  const needle = search.value.trim().toLowerCase()
  return baselines.items.filter(item => {
    const matchGroup = group.value === 'all' || baselineGroup(item) === group.value
    if (!matchGroup) return false
    const oneTime = isOneTimeBaseline(item)
    if (baselineType.value === 'regular' && oneTime) return false
    if (baselineType.value === 'one-time' && !oneTime) return false
    if (methodFilter.value !== 'all' && item.method.toUpperCase() !== methodFilter.value) return false
    if (priorityFilter.value !== 'all' && item.priority !== priorityFilter.value) return false
    if (originFilter.value !== 'all' && item.origin !== originFilter.value) return false
    const audit = baselines.auditByBaselineId.get(item.id)
    if (auditFilter.value === 'needs-review' && (!audit || audit.status === 'verified')) return false
    if (auditFilter.value !== 'all' && auditFilter.value !== 'needs-review' && audit?.status !== auditFilter.value) return false
    if (!needle) return true
    return [item.case_name, item.endpoint_summary, item.path, item.method, baselineGroup(item), baselineScopeLabel(item), ...item.tags]
      .join(' ')
      .toLowerCase()
      .includes(needle)
  })
})
const baselinePageCount = computed(() => Math.max(1, Math.ceil(filteredBaselines.value.length / BASELINE_PAGE_SIZE)))
const pagedBaselines = computed(() => {
  const start = (baselinePage.value - 1) * BASELINE_PAGE_SIZE
  return filteredBaselines.value.slice(start, start + BASELINE_PAGE_SIZE)
})
const filteredSelectedCount = computed(() => {
  const visible = new Set(filteredBaselines.value.map(item => item.id))
  return baselines.selectedIds.filter(id => visible.has(id)).length
})
const activeGroupItems = computed(() => (
  group.value === 'all' ? [] : baselines.items.filter(item => baselineGroup(item) === group.value)
))
const canManageCurrentGroup = computed(() => (
  group.value !== 'all' && group.value !== '未分组' && activeGroupItems.value.length > 0
))
const allFilteredSelected = computed(() => Boolean(
  filteredBaselines.value.length && filteredSelectedCount.value === filteredBaselines.value.length,
))
const selectedGroups = computed(() => [...new Set(baselines.selectedItems.map(item => baselineGroup(item)))])
const moveTargetName = computed(() => groupName.value.trim() || moveTargetGroup.value.trim())
const currentSafeAuditIds = computed(() => {
  if (!context.projectId || !context.environmentRevisionId || baselines.auditProjectId !== context.projectId) return []
  return baselines.items
    .filter(item => {
      const audit = baselines.auditByBaselineId.get(item.id)
      return item.status === 'active'
        && Boolean(audit && audit.status !== 'verified' && audit.execution.selectable)
        && audit?.environment_revision_id === context.environmentRevisionId
        && baselineSelection(item).selectable
    })
    .map(item => item.id)
})

watch([search, group, baselineType, methodFilter, priorityFilter, originFilter, auditFilter], () => {
  baselinePage.value = 1
})
watch(baselinePageCount, pageCount => {
  if (baselinePage.value > pageCount) baselinePage.value = pageCount
})

onMounted(async () => {
  await Promise.all([context.loadSavedContext(), context.loadOptions()])
  if (context.projectId) await tasks.restore(context.projectId)
  await loadBaselines()
})

function nullable(value: string): string | null {
  return value || null
}

async function changeProject(projectId: string | null): Promise<void> {
  context.selectProject(projectId)
  tasks.clear()
  baselines.clearSelection()
  await loadBaselines()
}

async function changeSource(sourceRevisionId: string | null): Promise<void> {
  context.selectSourceRevision(sourceRevisionId)
}

async function changeEnvironment(environmentRevisionId: string | null): Promise<void> {
  context.selectEnvironmentRevision(environmentRevisionId)
}

async function loadBaselines(): Promise<void> {
  localError.value = ''
  localMessage.value = ''
  auditFilter.value = 'all'
  if (!context.projectId) return
  await baselines.load({
    projectId: context.projectId,
  })
}

async function loadAssertionAudit(): Promise<void> {
  localError.value = ''
  localMessage.value = ''
  if (!context.projectId) {
    localError.value = '请先选择项目，再检查基线断言'
    return
  }
  await baselines.loadAudit(context.projectId)
  auditFilter.value = 'all'
}

function selectSafeAuditItems(): void {
  const safeIds = currentSafeAuditIds.value
  baselines.select(safeIds)
  localMessage.value = safeIds.length
    ? `已选择 ${safeIds.length} 条可安全复核基线；执行前仍需确认目标环境和请求影响`
    : '当前没有可安全批量复核的基线'
}

function toggleFiltered(): void {
  const visibleIds = filteredBaselines.value.map(item => item.id)
  if (allFilteredSelected.value) {
    const visible = new Set(visibleIds)
    baselines.select(baselines.selectedIds.filter(id => !visible.has(id)))
  } else {
    baselines.select([...baselines.selectedIds, ...visibleIds])
  }
}

async function saveScope(): Promise<void> {
  localError.value = ''
  try {
    await context.saveContext()
    if (context.error) throw new Error(context.error)
    await loadBaselines()
  } catch (error) {
    localError.value = error instanceof Error ? error.message : '测试范围保存失败'
  }
}

type BaselineActionValidation =
  | { ok: true; projectId: string; environmentRevisionId: string; sourceRevisionId: string }
  | { ok: false }

function validateBaselineAction(options: { requireEndpointIds?: boolean } = {}): BaselineActionValidation {
  const projectId = context.projectId
  if (!projectId) {
    localError.value = '请先选择项目'
    return { ok: false }
  }
  const environmentRevisionId = context.environmentRevisionId
  if (!environmentRevisionId) {
    localError.value = '请先选择执行环境；基线执行时只切换环境，不会因为接口版本变化而丢失'
    return { ok: false }
  }
  if (!baselines.selectedIds.length) {
    localError.value = '请先勾选要处理的基线用例'
    return { ok: false }
  }
  if (selectedBaselineScopeIssue.value) {
    localError.value = `${selectedBaselineScopeIssue.value}，历史基线仅支持查看、编辑和分组管理，不能创建新任务或执行`
    return { ok: false }
  }
  if (selectedAuditEnvironmentIssue.value) {
    localError.value = selectedAuditEnvironmentIssue.value
    return { ok: false }
  }
  if (selectedBaselineSourceRevisionIds.value.length > 1) {
    localError.value = '所选基线来自多个接口版本，请按来源版本分批保存或执行'
    return { ok: false }
  }
  const sourceRevisionId = selectedBaselineSourceRevisionIds.value[0] || baselineActionSourceRevisionId.value
  if (!sourceRevisionId) {
    localError.value = '所选基线缺少来源接口版本，无法保存或执行'
    return { ok: false }
  }
  if (options.requireEndpointIds && !baselines.selectedEndpointIds.length) {
    localError.value = '所选基线缺少接口信息，无法保存为任务'
    return { ok: false }
  }
  return { ok: true, projectId, environmentRevisionId, sourceRevisionId }
}

async function saveSelectedAsRegressionTask(): Promise<boolean> {
  localError.value = ''
  localMessage.value = ''
  const validation = validateBaselineAction({ requireEndpointIds: true })
  if (!validation.ok) return false
  try {
    const endpointIds = [...new Set(baselines.selectedEndpointIds)]
    await tasks.createSelection({
      projectId: validation.projectId,
      sourceRevisionId: validation.sourceRevisionId,
      environmentRevisionId: validation.environmentRevisionId,
    }, endpointIds, `${projectName.value}基线回归`)
    localMessage.value = `已保存基线回归任务：${baselines.selectedItems.length} 条基线，可在任务列表和定时任务中复用`
    return true
  } catch (error) {
    localError.value = error instanceof Error ? error.message : '基线回归任务保存失败'
    return false
  }
}

async function runSelectedBaselines(): Promise<void> {
  localError.value = ''
  localMessage.value = ''
  const validation = validateBaselineAction()
  if (!validation.ok) return
  if (!confirmApiExecution({
    action: '执行所选基线',
    environmentName: environmentName.value,
    targetName: projectName.value,
    caseCount: baselines.selectedIds.length,
  })) return
  try {
    const execution = await executions.runBaselines({
      projectId: validation.projectId,
      sourceRevisionId: validation.sourceRevisionId,
      environmentRevisionId: validation.environmentRevisionId,
      baselineIds: baselines.selectedIds,
    })
    await router.push({ name: 'runs', query: { executionId: execution.id } })
  } catch (error) {
    localError.value = error instanceof Error ? error.message : '基线任务执行失败'
  }
}

async function updateSelectedGroup(): Promise<void> {
  localError.value = ''
  localMessage.value = ''
  if (!baselines.selectedIds.length) {
    localError.value = '请先选择要移动的基线用例'
    return
  }
  const next = moveTargetName.value
  if (!next) {
    localError.value = '请选择目标分组，或输入新的分组名称'
    return
  }
  const affected = baselines.selectedItems.length
  try {
    await baselines.updateGroup(baselines.selectedIds, next)
    group.value = next
    groupName.value = ''
    moveTargetGroup.value = ''
    localMessage.value = `已将 ${affected} 条基线移动到“${next}”`
  } catch (error) {
    localError.value = error instanceof Error ? error.message : '基线分组保存失败'
  }
}

async function renameCurrentGroup(): Promise<void> {
  localError.value = ''
  localMessage.value = ''
  if (!canManageCurrentGroup.value) {
    localError.value = '请先选择一个自定义基线分组'
    return
  }
  const next = groupName.value.trim()
  if (!next) {
    localError.value = '请输入新的分组名称'
    return
  }
  try {
    await baselines.updateGroup(activeGroupItems.value.map(item => item.id), next)
    localMessage.value = `已将分组“${group.value}”重命名为“${next}”`
    group.value = next
    groupName.value = ''
  } catch (error) {
    localError.value = error instanceof Error ? error.message : '基线分组重命名失败'
  }
}

async function deleteCurrentGroup(): Promise<void> {
  localError.value = ''
  localMessage.value = ''
  if (!canManageCurrentGroup.value) {
    localError.value = '请先选择一个自定义基线分组'
    return
  }
  const previous = group.value
  const affected = activeGroupItems.value.length
  const confirmed = window.confirm(`删除分组“${previous}”？分组内基线会保留，并移回“未分组”。`)
  if (!confirmed) return
  try {
    await baselines.updateGroup(activeGroupItems.value.map(item => item.id), '未分组')
    localMessage.value = `已删除分组“${previous}”，${affected} 条基线已移回“未分组”`
    group.value = 'all'
    groupName.value = ''
  } catch (error) {
    localError.value = error instanceof Error ? error.message : '基线分组删除失败'
  }
}

async function editBaseline(item: ApiBaselineCase): Promise<void> {
  await router.push({
    name: 'workbench',
    query: {
      projectId: item.project_id,
      sourceRevisionId: item.source_revision_id,
      environmentRevisionId: item.environment_revision_id,
      endpointId: item.endpoint_id,
      caseVersionId: item.case_version_id,
    },
  })
}

async function openAssertionReviewDraft(
  item: ApiBaselineCase,
  audit: BaselineAssertionAuditItem,
): Promise<void> {
  localError.value = ''
  localMessage.value = ''
  try {
    const caseVersionId = audit.upgrade_draft_case_version_id
      || (await baselines.createAssertionUpgradeDraft(item.id)).id
    await router.push({
      name: 'workbench',
      query: {
        projectId: item.project_id,
        sourceRevisionId: item.source_revision_id,
        environmentRevisionId: item.environment_revision_id,
        endpointId: item.endpoint_id,
        caseVersionId,
      },
    })
  } catch (error) {
    localError.value = error instanceof Error ? error.message : '待复核版本生成失败'
  }
}

async function archiveBaseline(item: ApiBaselineCase): Promise<void> {
  const confirmed = window.confirm(`将“${item.case_name}”移出基线？用例草稿仍会保留，可在工作台继续编辑。`)
  if (!confirmed) return
  localError.value = ''
  localMessage.value = ''
  try {
    await baselines.archive(item.id)
    localMessage.value = `已将“${item.case_name}”移出基线`
  } catch (error) {
    localError.value = error instanceof Error ? error.message : '基线移出失败'
  }
}

function rowTitle(item: ApiBaselineCase): string {
  return item.endpoint_summary || item.case_name || item.path
}

function isOneTimeBaseline(item: ApiBaselineCase): boolean {
  return hasExplicitOneTimeMarker([item.case_name, baselineGroup(item), ...item.tags])
}

function auditEvidenceLabel(audit: BaselineAssertionAuditItem): string {
  if (audit.actual_http_status === null) return '未找到可解析的历史调试响应'
  const http = `HTTP ${audit.actual_http_status}`
  if (!audit.business_path) return `实际响应：${http}`
  const value = typeof audit.business_value === 'string'
    ? `“${audit.business_value}”`
    : JSON.stringify(audit.business_value)
  return `实际响应：${http} · ${audit.business_path} = ${value}`
}

function baselineOriginLabel(origin: string): string {
  if (origin === 'ai') return 'AI'
  if (origin === 'imported') return '平台导入'
  return '手工'
}

function baselineScopeLabel(item: ApiBaselineCase): string {
  return applicationBusinessLabel(item.app_package, item.app_name, item.business)
}

function baselineSelection(item: ApiBaselineCase) {
  return applicationBusinessSelection(item.app_package, item.business)
}

function sourceRevisionName(item: ApiBaselineCase): string {
  const source = selectedSourceById.value.get(item.source_revision_id)
  return source ? `${source.name} · v${source.revision_number}` : `来源版本 ${item.source_revision_id.slice(0, 8)}`
}

function adoptionReasonLabel(reason: string): string {
  const value = String(reason || '').trim()
  if (!value) return '已采纳为基线'
  if (value === 'passing debug evidence') return '已通过调试并采纳'
  return value
}
</script>

<template>
  <section class="workspace baselines-page">
    <header class="page-toolbar">
      <div>
        <p class="eyebrow">回归基线</p>
        <h1>基线用例</h1>
        <p class="page-subtitle">已调试通过并采纳的用例在这里统一查看。基线按项目固定保存，执行时再选择目标环境。</p>
      </div>
      <button class="icon-command" type="button" title="重新读取基线" :disabled="baselines.loading || !projectReady" @click="loadBaselines"><RefreshCw :class="{ 'is-spinning': baselines.loading }" :size="18" /></button>
    </header>

    <ContextBar
      :projects="context.projects"
      :source-revisions="context.sourceRevisions"
      :environment-revisions="context.environmentRevisions"
      :project-id="context.projectId"
      :source-revision-id="context.sourceRevisionId"
      :environment-revision-id="context.environmentRevisionId"
      :loading="context.loading || context.optionsLoading || baselines.loading"
      :saved="context.isSaved"
      save-label="保存执行环境"
      saved-label="执行环境已保存"
      @update:project-id="changeProject"
      @update:source-revision-id="changeSource"
      @update:environment-revision-id="changeEnvironment"
      @save="saveScope"
    />

    <section class="baseline-summary-grid" aria-label="基线概览">
      <div><span>项目</span><strong>{{ projectName }}</strong></div>
      <div><span>执行记录接口版本</span><strong>{{ selectedSourceName }}</strong><small>不筛选基线</small></div>
      <div><span>本次执行目标</span><strong>{{ environmentName }}</strong></div>
      <div><span>基线数量</span><strong>{{ baselines.items.length }} 条</strong></div>
      <div><span>已选择</span><strong>{{ baselines.selectedItems.length }} 条</strong></div>
    </section>

    <section class="baseline-audit-band" aria-label="基线断言检查">
      <div class="baseline-audit-heading">
        <ScanSearch :size="18" />
        <div>
          <strong>断言有效性检查</strong>
          <span>读取已保存的调试证据，核对 HTTP 状态、业务结果和现有断言；可从明确的成功证据生成下一版草稿，原基线不会被覆盖。</span>
        </div>
      </div>
      <div class="baseline-audit-actions">
        <button class="secondary-command" type="button" :disabled="baselines.auditLoading || !projectReady" @click="loadAssertionAudit">
          <ScanSearch :size="15" />{{ baselines.auditLoading ? '检查中' : '检查断言' }}
        </button>
        <button v-if="baselines.audit" class="secondary-command" type="button" :disabled="!currentSafeAuditIds.length" @click="selectSafeAuditItems">
          <ShieldCheck :size="15" />选择可安全复核项
        </button>
      </div>
      <div v-if="baselines.auditLoading" class="baseline-audit-progress" role="status">正在批量核对有效基线和最近一次调试证据…</div>
      <div v-else-if="baselines.audit" class="baseline-audit-summary" data-testid="baseline-audit-summary">
        <span>有效基线 <b>{{ baselines.audit.summary.total }}</b> 条</span>
        <span class="success">断言已精确 <b>{{ baselines.audit.summary.verified }}</b> 条</span>
        <span class="warning">需要复核 <b>{{ baselines.audit.summary.needs_review }}</b> 条</span>
        <span>可补精确断言 <b>{{ baselines.audit.summary.upgrade_available }}</b> 条</span>
        <span>HTTP 失败 <b>{{ baselines.audit.summary.http_failure }}</b> 条</span>
        <span>业务失败 <b>{{ baselines.audit.summary.business_failure }}</b> 条</span>
        <span>缺少领域断言 <b>{{ baselines.audit.summary.domain_assertion_required }}</b> 条</span>
        <span>证据不足 <b>{{ baselines.audit.summary.evidence_missing }}</b> 条</span>
        <span>当前环境可安全复核 <b>{{ currentSafeAuditIds.length }}</b> 条</span>
        <small>“生成待复核版本”只补充证据明确的精确业务断言；新版本仍需在原环境重新调试并采纳后，才会替换活动基线。</small>
      </div>
      <p v-if="baselines.auditError" class="inline-error">{{ baselines.auditError }}</p>
    </section>

    <section class="baseline-board">
      <aside class="baseline-filter-panel">
        <div class="search-box baseline-search"><Search :size="15" /><input v-model="search" placeholder="搜索用例、接口或路径" /></div>
        <div class="baseline-filter-grid" aria-label="基线筛选">
          <label><span>基线类型</span><select v-model="baselineType" data-testid="baseline-filter-type">
            <option value="all">全部类型</option><option value="regular">常规基线</option><option value="one-time">一次性</option>
          </select></label>
          <label><span>HTTP 方法</span><select v-model="methodFilter" data-testid="baseline-filter-method">
            <option value="all">全部方法</option><option v-for="item in methodOptions" :key="item" :value="item">{{ item }}</option>
          </select></label>
          <label><span>优先级</span><select v-model="priorityFilter" data-testid="baseline-filter-priority">
            <option value="all">全部优先级</option><option v-for="item in ['P0', 'P1', 'P2', 'P3']" :key="item" :value="item">{{ item }}</option>
          </select></label>
          <label><span>来源</span><select v-model="originFilter" data-testid="baseline-filter-origin">
            <option value="all">全部来源</option><option value="ai">AI</option><option value="imported">平台导入</option><option value="manual">手工</option>
          </select></label>
          <label><span>断言检查</span><select v-model="auditFilter" data-testid="baseline-filter-audit" :disabled="!baselines.audit">
            <option value="all">全部结果</option><option value="needs-review">需要复核</option><option value="verified">断言已精确</option>
            <option value="upgrade_available">可补精确断言</option><option value="http_failure">实际 HTTP 失败</option>
            <option value="business_failure">实际业务失败</option><option value="domain_assertion_required">缺少领域断言</option><option value="evidence_missing">证据不足</option>
          </select></label>
        </div>
        <div class="baseline-group-list" aria-label="基线分组">
          <button type="button" :class="{ active: group === 'all' }" @click="group = 'all'">
            <span>全部基线</span><strong>{{ baselines.items.length }}</strong>
          </button>
          <button v-for="item in baselines.groups" :key="item" type="button" :class="{ active: group === item }" @click="group = item">
            <span>{{ item }}</span><strong>{{ baselines.items.filter(row => baselineGroup(row) === item).length }}</strong>
          </button>
        </div>
      </aside>

      <main class="baseline-table-panel">
        <header class="baseline-action-bar">
          <div>
            <ShieldCheck :size="17" />
            <strong>{{ filteredBaselines.length }} 条基线</strong>
            <span>{{ filteredSelectedCount }} 条已选</span>
          </div>
          <div>
            <button class="secondary-command" type="button" :disabled="!filteredBaselines.length" @click="toggleFiltered">{{ allFilteredSelected ? '取消当前筛选' : '全选当前筛选' }}</button>
            <button class="secondary-command" type="button" :disabled="!baselines.selectedIds.length" @click="baselines.clearSelection">清空选择</button>
            <button class="primary-command" type="button" :disabled="tasks.saving || !baselineActionReady" @click="saveSelectedAsRegressionTask"><ListPlus :size="15" />{{ tasks.saving ? '保存中' : '保存为基线回归任务' }}</button>
            <button class="primary-command" type="button" :disabled="executions.baselineStarting || !baselineActionReady" @click="runSelectedBaselines"><Play :size="15" />{{ executions.baselineStarting ? '创建执行中' : '按当前环境执行所选基线' }}</button>
            <small class="baseline-action-hint" :class="{ warning: selectedBaselineActionIssue }">{{ selectedBaselineActionIssue || '保存会创建独立基线回归任务；立即执行只使用当前执行环境，不修改工作台任务。' }}</small>
          </div>
        </header>
        <div class="baseline-group-editor" aria-label="基线分组编辑">
          <div>
            <strong>基线分组</strong>
            <span>可移动到已有分组，也可输入新名称创建分组；选中左侧分组后可重命名或删除。</span>
          </div>
          <div class="baseline-group-controls">
            <label>
              <span>目标分组</span>
              <select v-model="moveTargetGroup" data-testid="baseline-move-target">
                <option value="">选择已有分组</option>
                <option v-for="item in baselines.groups" :key="item" :value="item">{{ item }}</option>
              </select>
            </label>
            <label>
              <span>新分组/重命名</span>
              <input v-model="groupName" placeholder="例如：发版冒烟、收藏链路、登录鉴权" />
            </label>
          </div>
          <div class="baseline-group-actions">
            <button class="secondary-command" type="button" data-testid="baseline-move-selected" :disabled="!baselines.selectedIds.length || !moveTargetName" @click="updateSelectedGroup">
              移动所选
            </button>
            <button class="secondary-command" type="button" :disabled="!canManageCurrentGroup || !groupName.trim()" @click="renameCurrentGroup">
              重命名分组
            </button>
            <button class="secondary-command danger" type="button" :disabled="!canManageCurrentGroup" @click="deleteCurrentGroup">
              删除分组
            </button>
          </div>
        </div>

        <div v-if="baselines.loading" class="section-empty">正在读取基线用例…</div>
        <div v-else-if="!context.projectId" class="section-empty">先选择项目，再查看该项目沉淀的基线。</div>
        <div v-else-if="!filteredBaselines.length" class="section-empty">{{ baselines.items.length ? '当前筛选下没有匹配基线，请调整类型、方法、优先级、来源或搜索条件。' : '该项目暂无基线。基线按项目固定保存，切换接口版本或执行环境不会影响这里；请先在工作台调试通过后采纳为基线。' }}</div>
        <div v-else class="baseline-table" role="table" aria-label="基线用例列表">
          <div class="baseline-table-head" role="row">
            <span></span><span>用例</span><span>接口</span><span>分组</span><span>版本</span><span>采纳时间</span><span>操作</span>
          </div>
          <div v-for="item in pagedBaselines" :key="item.id" class="baseline-row" role="row">
            <label class="baseline-checkbox">
              <input type="checkbox" :data-testid="`baseline-select-${item.id}`" :checked="baselines.selectedIds.includes(item.id)" @change="baselines.toggle(item.id)" />
            </label>
            <span class="baseline-case-copy">
              <strong>{{ item.case_name }} <b class="baseline-business-pill" :class="`business-${item.business || 'unset'}`">{{ baselineScopeLabel(item) }}</b> <b v-if="isOneTimeBaseline(item)" :data-testid="`baseline-one-time-${item.id}`" class="baseline-one-time-pill">一次性</b></strong>
              <small>
                <b v-if="item.status !== 'active'" class="baseline-status-pill">历史版本</b>
                <b v-if="!baselineSelection(item).selectable" class="baseline-status-pill">{{ baselineSelection(item).reason }}</b>
                {{ adoptionReasonLabel(item.adoption_reason) }}
              </small>
              <small v-if="baselines.auditByBaselineId.get(item.id)" class="baseline-audit-result">
                <b :class="['baseline-audit-status', `status-${baselines.auditByBaselineId.get(item.id)!.status}`]">{{ baselines.auditByBaselineId.get(item.id)!.status_label }}</b>
                <span>{{ auditEvidenceLabel(baselines.auditByBaselineId.get(item.id)!) }}</span>
                <span>{{ baselines.auditByBaselineId.get(item.id)!.reason }}</span>
                <span :class="{ warning: !baselines.auditByBaselineId.get(item.id)!.execution.selectable }">{{ baselines.auditByBaselineId.get(item.id)!.execution.label }}：{{ baselines.auditByBaselineId.get(item.id)!.execution.reason }}</span>
                <button
                  v-if="baselines.auditByBaselineId.get(item.id)!.status === 'upgrade_available'"
                  class="tiny-command baseline-audit-upgrade"
                  type="button"
                  :data-testid="`baseline-upgrade-${item.id}`"
                  :disabled="baselines.creatingUpgradeBaselineId === item.id"
                  @click="openAssertionReviewDraft(item, baselines.auditByBaselineId.get(item.id)!)"
                >
                  <FilePlus2 :size="13" />
                  {{ baselines.auditByBaselineId.get(item.id)!.upgrade_draft_case_version_id ? '继续复核新版本' : (baselines.creatingUpgradeBaselineId === item.id ? '生成中' : '生成待复核版本') }}
                </button>
              </small>
            </span>
            <span class="baseline-endpoint-copy">
              <b><span :class="['method-badge', `method-${item.method.toLowerCase()}`]">{{ item.method }}</span>{{ rowTitle(item) }}</b>
              <code>{{ item.path }}</code>
            </span>
            <span>{{ baselineGroup(item) }}</span>
            <span>{{ item.priority }} · 用例 v{{ item.case_version }} · {{ baselineOriginLabel(item.origin) }}<small>来源版本：{{ sourceRevisionName(item) }}</small></span>
            <time>{{ new Date(item.adopted_at).toLocaleString('zh-CN') }}</time>
            <span class="baseline-row-actions">
              <button class="tiny-command" type="button" title="编辑用例" @click="editBaseline(item)"><Edit3 :size="14" />编辑</button>
              <button class="tiny-command danger" type="button" title="移出基线" @click="archiveBaseline(item)"><Trash2 :size="14" />移出</button>
            </span>
          </div>
          <nav v-if="baselinePageCount > 1" class="list-pagination" aria-label="基线列表分页">
            <button type="button" :disabled="baselinePage === 1" @click="baselinePage -= 1">上一页</button>
            <span>第 {{ baselinePage }} / {{ baselinePageCount }} 页</span>
            <button data-testid="baseline-page-next" type="button" :disabled="baselinePage === baselinePageCount" @click="baselinePage += 1">下一页</button>
          </nav>
        </div>
      </main>
    </section>

    <p v-if="context.error || baselines.error || localError" class="inline-error">{{ context.error || baselines.error || localError }}</p>
    <p v-if="localMessage" class="setup-success"><ShieldCheck :size="16" />{{ localMessage }}<span v-if="selectedGroups.length">覆盖分组：{{ selectedGroups.join('、') }}</span></p>
  </section>
</template>
