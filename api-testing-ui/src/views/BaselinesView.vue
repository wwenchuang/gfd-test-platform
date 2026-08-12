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
const localError = ref('')
const localMessage = ref('')

const contextReady = computed(() => Boolean(context.projectId && context.sourceRevisionId && context.environmentRevisionId))
const projectName = computed(() => context.projects.find(item => item.id === context.projectId)?.name || '未选择项目')
const sourceName = computed(() => {
  const source = context.sourceRevisions.find(item => item.id === context.sourceRevisionId)
  return source ? `${source.name} · v${source.revision_number}` : '未选择接口版本'
})
const environmentName = computed(() => {
  const environment = context.environmentRevisions.find(item => item.id === context.environmentRevisionId)
  return environment ? `${environment.name} · v${environment.revision}` : '未选择环境'
})
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
const allFilteredSelected = computed(() => Boolean(
  filteredBaselines.value.length && filteredSelectedCount.value === filteredBaselines.value.length,
))
const selectedGroups = computed(() => [...new Set(baselines.selectedItems.map(item => baselineGroup(item)))])

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
  baselines.clearSelection()
  await loadBaselines()
}

async function changeEnvironment(environmentRevisionId: string | null): Promise<void> {
  context.selectEnvironmentRevision(environmentRevisionId)
  baselines.clearSelection()
  await loadBaselines()
}

async function loadBaselines(): Promise<void> {
  localError.value = ''
  localMessage.value = ''
  if (!context.projectId || !context.sourceRevisionId || !context.environmentRevisionId) return
  await baselines.load({
    projectId: context.projectId,
    sourceRevisionId: context.sourceRevisionId,
    environmentRevisionId: context.environmentRevisionId,
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

async function addSelectedToTask(): Promise<boolean> {
  localError.value = ''
  localMessage.value = ''
  if (!context.projectId || !context.sourceRevisionId || !context.environmentRevisionId) {
    localError.value = '请先选择项目、接口版本和执行环境'
    return false
  }
  if (!baselines.selectedEndpointIds.length) {
    localError.value = '请先勾选要加入任务的基线用例'
    return false
  }
  try {
    await context.saveContext()
    if (context.error) throw new Error(context.error)
    const restored = await tasks.restore(context.projectId)
    const existing = restored?.project_id === context.projectId
      && restored.source_revision_id === context.sourceRevisionId
      && restored.environment_revision_id === context.environmentRevisionId
      ? restored.selected_endpoint_ids
      : []
    const endpointIds = [...new Set([...existing, ...baselines.selectedEndpointIds])]
    await tasks.saveSelection({
      projectId: context.projectId,
      sourceRevisionId: context.sourceRevisionId,
      environmentRevisionId: context.environmentRevisionId,
    }, endpointIds, `${projectName.value}基线回归`)
    localMessage.value = `已将 ${baselines.selectedItems.length} 条基线加入当前任务`
    return true
  } catch (error) {
    localError.value = error instanceof Error ? error.message : '基线加入任务失败'
    return false
  }
}

async function runSelectedTask(): Promise<void> {
  localError.value = ''
  localMessage.value = ''
  if (!context.projectId || !context.sourceRevisionId || !context.environmentRevisionId) {
    localError.value = '请先选择项目、接口版本和执行环境'
    return
  }
  if (!baselines.selectedIds.length) {
    localError.value = '请先勾选要执行的基线用例'
    return
  }
  try {
    await context.saveContext()
    if (context.error) throw new Error(context.error)
    const execution = await executions.runBaselines({
      projectId: context.projectId,
      sourceRevisionId: context.sourceRevisionId,
      environmentRevisionId: context.environmentRevisionId,
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
  try {
    await baselines.updateGroup(baselines.selectedIds, groupName.value)
    const next = groupName.value.trim()
    group.value = next
    groupName.value = ''
    localMessage.value = `已将 ${baselines.selectedItems.length} 条基线归入“${next}”`
  } catch (error) {
    localError.value = error instanceof Error ? error.message : '基线分组保存失败'
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
</script>

<template>
  <section class="workspace baselines-page">
    <header class="page-toolbar">
      <div>
        <p class="eyebrow">API BASELINES</p>
        <h1>基线用例</h1>
        <p class="page-subtitle">已调试通过并采纳的用例在这里统一查看，可批量加入任务用于发版回归。</p>
      </div>
      <button class="icon-command" type="button" title="重新读取基线" :disabled="baselines.loading || !contextReady" @click="loadBaselines"><RefreshCw :class="{ 'is-spinning': baselines.loading }" :size="18" /></button>
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
      @update:project-id="changeProject"
      @update:source-revision-id="changeSource"
      @update:environment-revision-id="changeEnvironment"
      @save="saveScope"
    />

    <section class="baseline-summary-grid" aria-label="基线概览">
      <div><span>项目</span><strong>{{ projectName }}</strong></div>
      <div><span>接口版本</span><strong>{{ sourceName }}</strong></div>
      <div><span>执行环境</span><strong>{{ environmentName }}</strong></div>
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
            <button class="primary-command" type="button" :disabled="tasks.saving || !baselines.selectedIds.length" @click="addSelectedToTask"><ListPlus :size="15" />{{ tasks.saving ? '加入中' : '加入当前任务' }}</button>
            <button class="primary-command" type="button" :disabled="executions.baselineStarting || !baselines.selectedIds.length" @click="runSelectedTask"><Play :size="15" />{{ executions.baselineStarting ? '创建执行中' : '执行所选基线' }}</button>
          </div>
        </header>
        <div class="baseline-group-editor" aria-label="基线分组编辑">
          <div>
            <strong>基线分组</strong>
            <span>选择基线后输入分组名，可新建分组或移动到已有分组。</span>
          </div>
          <input v-model="groupName" placeholder="例如：发版冒烟、收藏链路、登录鉴权" />
          <button class="secondary-command" type="button" :disabled="!baselines.selectedIds.length || !groupName.trim()" @click="updateSelectedGroup">
            保存分组
          </button>
        </div>

        <div v-if="baselines.loading" class="section-empty">正在读取基线用例…</div>
        <div v-else-if="!contextReady" class="section-empty">先选择项目、接口版本和执行环境，再查看对应基线。</div>
        <div v-else-if="!filteredBaselines.length" class="section-empty">当前范围暂无基线。请先在工作台调试通过后采纳为基线。</div>
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
              <small>{{ item.adoption_reason || '已采纳为基线' }}</small>
            </span>
            <span class="baseline-endpoint-copy">
              <b><span :class="['method-badge', `method-${item.method.toLowerCase()}`]">{{ item.method }}</span>{{ rowTitle(item) }}</b>
              <code>{{ item.path }}</code>
            </span>
            <span>{{ baselineGroup(item) }}</span>
            <span>{{ item.priority }} · v{{ item.case_version }} · {{ item.origin === 'ai' ? 'AI' : '手工' }}</span>
            <time>{{ new Date(item.adopted_at).toLocaleString('zh-CN') }}</time>
            <span class="baseline-row-actions">
              <button class="tiny-command" type="button" title="编辑用例" @click="editBaseline(item)"><Edit3 :size="14" />编辑</button>
              <button class="tiny-command danger" type="button" title="移出基线" @click="archiveBaseline(item)"><Trash2 :size="14" />移出</button>
            </span>
          </div>
        </div>
      </main>
    </section>

    <p v-if="context.error || baselines.error || tasks.error || localError" class="inline-error">{{ context.error || baselines.error || tasks.error || localError }}</p>
    <p v-if="localMessage" class="setup-success"><ShieldCheck :size="16" />{{ localMessage }}<span v-if="selectedGroups.length">覆盖分组：{{ selectedGroups.join('、') }}</span></p>
  </section>
</template>
