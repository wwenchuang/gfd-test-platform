<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { Edit3, ListPlus, Play, RefreshCw, Search, ShieldCheck, Trash2 } from 'lucide-vue-next'
import { useRouter } from 'vue-router'

import ContextBar from '../components/ContextBar.vue'
import type { ApiBaselineCase } from '../api/contracts'
import { baselineGroup, useBaselinesStore } from '../stores/baselines'
import { useContextStore } from '../stores/context'
import { useExecutionsStore } from '../stores/executions'
import { useTasksStore } from '../stores/tasks'

const context = useContextStore()
const baselines = useBaselinesStore()
const executions = useExecutionsStore()
const tasks = useTasksStore()
const router = useRouter()
const search = ref('')
const group = ref('all')
const groupName = ref('')
const moveTargetGroup = ref('')
const localError = ref('')
const localMessage = ref('')

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
const baselineActionReady = computed(() => Boolean(context.projectId && context.environmentRevisionId && baselines.selectedIds.length))
const selectedSourceById = computed(() => new Map(context.sourceRevisions.map(item => [item.id, item])))
const filteredBaselines = computed(() => {
  const needle = search.value.trim().toLowerCase()
  return baselines.items.filter(item => {
    const matchGroup = group.value === 'all' || baselineGroup(item) === group.value
    if (!matchGroup) return false
    if (!needle) return true
    return [item.case_name, item.endpoint_summary, item.path, item.method, ...item.tags]
      .join(' ')
      .toLowerCase()
      .includes(needle)
  })
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
  if (!context.projectId) return
  await baselines.load({
    projectId: context.projectId,
  })
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
      endpointId: item.endpoint_id,
      caseVersionId: item.case_version_id,
    },
  })
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

function sourceRevisionName(item: ApiBaselineCase): string {
  const source = selectedSourceById.value.get(item.source_revision_id)
  return source ? `${source.name} · v${source.revision_number}` : `来源版本 ${item.source_revision_id.slice(0, 8)}`
}
</script>

<template>
  <section class="workspace baselines-page">
    <header class="page-toolbar">
      <div>
        <p class="eyebrow">API BASELINES</p>
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

    <section class="baseline-board">
      <aside class="baseline-filter-panel">
        <div class="search-box baseline-search"><Search :size="15" /><input v-model="search" placeholder="搜索用例、接口或路径" /></div>
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
            <small class="baseline-action-hint">保存会创建独立基线回归任务；立即执行只使用当前执行环境，不修改工作台任务。</small>
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
        <div v-else-if="!filteredBaselines.length" class="section-empty">该项目暂无基线。基线按项目固定保存，切换接口版本或执行环境不会影响这里；请先在工作台调试通过后采纳为基线。</div>
        <div v-else class="baseline-table" role="table" aria-label="基线用例列表">
          <div class="baseline-table-head" role="row">
            <span></span><span>用例</span><span>接口</span><span>分组</span><span>版本</span><span>采纳时间</span><span>操作</span>
          </div>
          <div v-for="item in filteredBaselines" :key="item.id" class="baseline-row" role="row">
            <label class="baseline-checkbox">
              <input type="checkbox" :checked="baselines.selectedIds.includes(item.id)" @change="baselines.toggle(item.id)" />
            </label>
            <span class="baseline-case-copy">
              <strong>{{ item.case_name }}</strong>
              <small>
                <b v-if="item.status !== 'active'" class="baseline-status-pill">历史版本</b>
                {{ item.adoption_reason || '已采纳为基线' }}
              </small>
            </span>
            <span class="baseline-endpoint-copy">
              <b><span :class="['method-badge', `method-${item.method.toLowerCase()}`]">{{ item.method }}</span>{{ rowTitle(item) }}</b>
              <code>{{ item.path }}</code>
            </span>
            <span>{{ baselineGroup(item) }}</span>
            <span>{{ item.priority }} · 用例 v{{ item.case_version }} · {{ item.origin === 'ai' ? 'AI' : '手工' }}<small>来源版本：{{ sourceRevisionName(item) }}</small></span>
            <time>{{ new Date(item.adopted_at).toLocaleString('zh-CN') }}</time>
            <span class="baseline-row-actions">
              <button class="tiny-command" type="button" title="编辑用例" @click="editBaseline(item)"><Edit3 :size="14" />编辑</button>
              <button class="tiny-command danger" type="button" title="移出基线" @click="archiveBaseline(item)"><Trash2 :size="14" />移出</button>
            </span>
          </div>
        </div>
      </main>
    </section>

    <p v-if="context.error || baselines.error || localError" class="inline-error">{{ context.error || baselines.error || localError }}</p>
    <p v-if="localMessage" class="setup-success"><ShieldCheck :size="16" />{{ localMessage }}<span v-if="selectedGroups.length">覆盖分组：{{ selectedGroups.join('、') }}</span></p>
  </section>
</template>
