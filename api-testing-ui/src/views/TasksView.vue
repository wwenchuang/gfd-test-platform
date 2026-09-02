<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { ArrowLeft, Edit3, Play, Save, Trash2 } from 'lucide-vue-next'
import { useRouter } from 'vue-router'

import type { ApiEndpoint, ApiTestTask } from '../api/contracts'
import ContextBar from '../components/ContextBar.vue'
import TaskListPanel from '../components/TaskListPanel.vue'
import { useAssetsStore } from '../stores/assets'
import { useBaselinesStore } from '../stores/baselines'
import { useContextStore } from '../stores/context'
import { useTasksStore } from '../stores/tasks'
import { compareGroupNames, endpointGroupName } from '../utils/endpointGroups'
import { confirmApiExecution } from '../utils/executionConfirmation'
import { applicationBusinessLabel } from '../utils/testApplications'
import { compactDateTime, taskLatestResult, taskStateLabel, taskRunBlockReason } from '../utils/taskPresentation'

const context = useContextStore()
const assets = useAssetsStore()
const baselines = useBaselinesStore()
const tasks = useTasksStore()
const router = useRouter()

const localError = ref('')
const taskNameDraft = ref('')
const mobileDetailOpen = ref(false)
const taskEndpointSearch = ref('')
const taskEndpointPage = ref(1)
const TASK_ENDPOINT_PAGE_SIZE = 50

const activeTask = computed(() => tasks.task)
const runBlockReason = computed(() => activeTask.value ? taskRunBlockReason(activeTask.value) : '')
const taskEnvironmentNames = computed(() => Object.fromEntries(
  context.environmentRevisions.map(item => [item.id, `${item.name} · v${item.revision}`]),
))
const selectedEndpoints = computed(() => {
  const selected = new Set(activeTask.value?.selected_endpoint_ids || [])
  return assets.endpoints.filter(endpoint => selected.has(endpoint.id))
})
const filteredSelectedEndpoints = computed(() => {
  const query = taskEndpointSearch.value.trim().toLocaleLowerCase('zh-CN')
  if (!query) return selectedEndpoints.value
  return selectedEndpoints.value.filter(endpoint => [
    endpoint.method,
    endpoint.summary,
    endpoint.path,
    ...(endpoint.tags || []),
  ].some(value => String(value || '').toLocaleLowerCase('zh-CN').includes(query)))
})
const taskEndpointPageCount = computed(() => Math.max(
  1,
  Math.ceil(filteredSelectedEndpoints.value.length / TASK_ENDPOINT_PAGE_SIZE),
))
const pagedSelectedEndpoints = computed(() => {
  const start = (taskEndpointPage.value - 1) * TASK_ENDPOINT_PAGE_SIZE
  return filteredSelectedEndpoints.value.slice(start, start + TASK_ENDPOINT_PAGE_SIZE)
})
const groupedSelectedEndpoints = computed(() => {
  const grouped = new Map<string, ApiEndpoint[]>()
  for (const endpoint of pagedSelectedEndpoints.value) {
    const group = endpointGroupName(endpoint)
    grouped.set(group, [...(grouped.get(group) || []), endpoint])
  }
  return [...grouped.entries()].sort(([left], [right]) => compareGroupNames(left, right))
})
const taskApplicationScope = computed(() => {
  const selected = new Set(activeTask.value?.selected_endpoint_ids || [])
  const labels = baselines.items
    .filter(item => item.status === 'active' && selected.has(item.endpoint_id))
    .map(item => applicationBusinessLabel(item.app_package, item.app_name, item.business))
  if (labels.length) return [...new Set(labels)].join('；')
  if (baselines.loading) return '正在读取活动基线…'
  return activeTask.value?.runnable_baseline_count ? '活动基线归属未记录' : '尚无可执行基线'
})
const sourceLabel = computed(() => {
  const source = context.sourceRevisions.find(item => item.id === activeTask.value?.source_revision_id)
  return source ? `${source.name} · v${source.revision_number}` : activeTask.value?.source_revision_id || '未选择'
})
const environmentLabel = computed(() => {
  const environmentId = context.environmentRevisionId || activeTask.value?.environment_revision_id
  const environment = context.environmentRevisions.find(item => item.id === environmentId)
  return environment ? `${environment.name} · v${environment.revision}` : environmentId || '未选择'
})

onMounted(async () => {
  await Promise.all([context.loadSavedContext(), context.loadOptions()])
  if (context.projectId) {
    await Promise.all([
      tasks.list(context.projectId),
      baselines.load({ projectId: context.projectId }),
    ])
    const restored = await tasks.restore(context.projectId)
    if (restored) await selectTask(restored.id, false)
  }
  if (!tasks.task && context.sourceRevisionId) await assets.load(context.sourceRevisionId)
})

watch(() => tasks.task?.name, name => {
  taskNameDraft.value = name || ''
}, { immediate: true })

watch([() => tasks.task?.id, taskEndpointSearch], () => {
  taskEndpointPage.value = 1
})

watch(taskEndpointPageCount, count => {
  if (taskEndpointPage.value > count) taskEndpointPage.value = count
})

function changeProject(projectId: string | null): void {
  context.selectProject(projectId)
  tasks.clear()
  mobileDetailOpen.value = false
  tasks.tasks = []
  assets.endpoints = []
  baselines.items = []
  localError.value = ''
  if (projectId) {
    void Promise.all([
      tasks.list(projectId),
      baselines.load({ projectId }),
    ])
  }
}

async function changeSource(sourceRevisionId: string | null): Promise<void> {
  context.selectSourceRevision(sourceRevisionId)
  assets.endpoints = []
  if (sourceRevisionId) await assets.load(sourceRevisionId)
}

function changeEnvironment(environmentRevisionId: string | null): void {
  context.selectEnvironmentRevision(environmentRevisionId)
}

async function saveScope(): Promise<void> {
  localError.value = ''
  try {
    await context.saveContext()
    if (context.error) throw new Error(context.error)
  } catch (error) {
    localError.value = error instanceof Error ? error.message : '管理范围保存失败'
  }
}

async function selectTask(taskId: string, revealDetail = true): Promise<void> {
  localError.value = ''
  const task = tasks.select(taskId)
  if (!task) return
  ensureTaskContextOptions(task, context.environmentRevisionId || task.environment_revision_id)
  context.restoreExecutionContext({
    project_id: task.project_id,
    source_revision_id: task.source_revision_id,
    environment_revision_id: context.environmentRevisionId || task.environment_revision_id,
  })
  await assets.load(task.source_revision_id)
  mobileDetailOpen.value = revealDetail
}

async function renameTask(): Promise<void> {
  if (!tasks.task) return
  localError.value = ''
  try {
    await tasks.rename(tasks.task.id, taskNameDraft.value)
  } catch (error) {
    localError.value = error instanceof Error ? error.message : '任务名称保存失败'
  }
}

async function runTask(taskId?: string): Promise<void> {
  if (taskId) await selectTask(taskId)
  if (!tasks.task) {
    localError.value = '请先选择任务'
    return
  }
  if (tasks.running || runBlockReason.value) {
    localError.value = runBlockReason.value || '正在创建执行，请勿重复提交'
    return
  }
  const environmentRevisionId = context.environmentRevisionId || tasks.task.environment_revision_id
  const environmentName = context.environmentRevisions.find(item => item.id === environmentRevisionId)?.name || '任务保存环境'
  if (!confirmApiExecution({
    action: '执行任务',
    environmentName,
    targetName: tasks.task.name,
    caseCount: tasks.task.runnable_baseline_count,
  })) return
  localError.value = ''
  try {
    const execution = await tasks.runCurrent(environmentRevisionId)
    await router.push({ name: 'runs', query: { executionId: execution.id } })
  } catch (error) {
    localError.value = error instanceof Error ? error.message : '任务执行失败'
  }
}

async function deleteTask(task: ApiTestTask): Promise<void> {
  const confirmed = window.confirm(`删除任务“${task.name}”？任务关联的用例、基线和历史执行记录会保留。`)
  if (!confirmed) return
  localError.value = ''
  try {
    const deletingActiveTask = tasks.task?.id === task.id
    await tasks.remove(task.id)
    if (deletingActiveTask) mobileDetailOpen.value = false
  } catch (error) {
    localError.value = error instanceof Error ? error.message : '任务删除失败'
  }
}

async function editTaskInWorkbench(): Promise<void> {
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

async function createTaskInWorkbench(): Promise<void> {
  tasks.clear()
  await router.push({ name: 'workbench', query: { newTask: '1' } })
}

async function openLatestExecution(): Promise<void> {
  if (!activeTask.value?.latest_execution_id) return
  await router.push({ name: 'runs', query: { executionId: activeTask.value.latest_execution_id } })
}

function ensureTaskContextOptions(task: ApiTestTask, environmentRevisionId: string | null): void {
  if (!context.sourceRevisions.some(item => item.id === task.source_revision_id)) {
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
  if (environmentRevisionId && !context.environmentRevisions.some(item => item.id === environmentRevisionId)) {
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
}
</script>

<template>
  <section class="workspace management-page tasks-page" data-testid="tasks-page">
    <header class="page-toolbar">
      <div>
        <p class="eyebrow">任务管理</p>
        <h1>任务管理</h1>
        <p class="page-subtitle">独立维护测试任务，集中处理编辑、执行、删除和范围调整。</p>
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
    <p v-if="context.error || tasks.error || assets.error || baselines.error || localError" class="inline-error">{{ context.error || tasks.error || assets.error || baselines.error || localError }}</p>
    <div :class="['management-shell', 'task-management-shell', { 'mobile-detail-open': mobileDetailOpen }]" data-testid="task-management-shell">
      <TaskListPanel
        :tasks="tasks.tasks"
        :active-task-id="tasks.task?.id || ''"
        :environment-names="taskEnvironmentNames"
        :loading="tasks.loading"
        :saving="tasks.saving"
        :running="tasks.running"
        @new="createTaskInWorkbench"
        @select="selectTask"
        @run="runTask"
        @delete="deleteTask"
      />
      <main class="management-detail task-detail-panel">
        <template v-if="activeTask">
          <header class="management-detail-head">
            <button data-testid="management-back-to-list" class="management-back-to-list" type="button" @click="mobileDetailOpen = false"><ArrowLeft :size="16" />返回任务列表</button>
            <div>
              <p>当前选中任务</p>
              <h2 data-testid="selected-task-title">{{ activeTask.name }}</h2>
            </div>
            <span data-testid="selected-task-state" :class="`task-state-pill task-state-${activeTask.state}`">{{ taskStateLabel(activeTask.state, activeTask.runnable_baseline_count) }}</span>
          </header>
          <div class="management-detail-body">
            <div class="task-detail-form">
              <label>任务名称
                <input v-model="taskNameDraft" data-testid="task-detail-name" />
              </label>
              <div class="detail-action-row">
                <button class="secondary-command" type="button" :disabled="tasks.saving" @click="renameTask"><Save :size="15" />保存名称</button>
                <button class="secondary-command" type="button" @click="editTaskInWorkbench"><Edit3 :size="15" />编辑范围</button>
                <button data-testid="task-detail-run" class="primary-command" type="button" :disabled="tasks.running || Boolean(runBlockReason)" :title="runBlockReason" @click="runTask()">
                  <Play :size="15" />执行任务
                </button>
                <button v-if="activeTask.latest_execution_id" data-testid="task-latest-execution" class="secondary-command" type="button" @click="openLatestExecution">
                  最近执行
                </button>
                <button data-testid="task-detail-delete" class="secondary-command danger-command" type="button" :disabled="tasks.saving || tasks.running" @click="deleteTask(activeTask)">
                  <Trash2 :size="15" />删除
                </button>
              </div>
              <p v-if="runBlockReason" data-testid="task-run-block-reason" class="section-empty" role="status">{{ runBlockReason }}</p>
            </div>
            <section class="management-summary-grid" aria-label="任务概要">
              <div><span>接口版本</span><strong>{{ sourceLabel }}</strong></div>
              <div><span>执行环境</span><strong>{{ environmentLabel }}</strong></div>
              <div><span>应用与业务</span><strong>{{ taskApplicationScope }}</strong></div>
              <div><span>接口数量</span><strong>{{ activeTask.selected_endpoint_ids.length }}</strong></div>
              <div><span>可执行基线</span><strong>{{ activeTask.runnable_baseline_count }}</strong></div>
              <div><span>最近结果</span><strong>{{ taskLatestResult(activeTask) }}</strong></div>
              <div><span>最近更新</span><strong>{{ compactDateTime(activeTask.updated_at) }}</strong></div>
            </section>
            <section class="task-endpoint-section">
              <header><h3>任务接口范围</h3><span>{{ filteredSelectedEndpoints.length }} / {{ activeTask.selected_endpoint_ids.length }}</span></header>
              <div class="task-endpoint-toolbar">
                <input v-model="taskEndpointSearch" data-testid="task-endpoint-search" type="search" placeholder="搜索接口名称、路径或分组" />
                <span data-testid="task-endpoint-page-status">第 {{ taskEndpointPage }} / {{ taskEndpointPageCount }} 页 · {{ filteredSelectedEndpoints.length }} 条匹配</span>
              </div>
              <div class="task-endpoint-list">
                <template v-for="[group, endpoints] in groupedSelectedEndpoints" :key="group">
                  <h4>{{ group }} <span>{{ endpoints.length }}</span></h4>
                  <article v-for="endpoint in endpoints" :key="endpoint.id" class="task-endpoint-row">
                    <span :class="['method-badge', `method-${endpoint.method.toLowerCase()}`]">{{ endpoint.method }}</span>
                    <div><strong>{{ endpoint.summary || endpoint.path }}</strong><code>{{ endpoint.path }}</code></div>
                  </article>
                </template>
                <p v-if="!filteredSelectedEndpoints.length" class="section-empty">{{ assets.state === 'loading' ? '正在读取任务接口...' : taskEndpointSearch ? '没有匹配的任务接口。' : '当前接口版本里未找到该任务保存的接口。' }}</p>
              </div>
              <div v-if="taskEndpointPageCount > 1" class="list-pagination task-endpoint-pagination">
                <button data-testid="task-endpoint-previous" type="button" :disabled="taskEndpointPage <= 1" @click="taskEndpointPage -= 1">上一页</button>
                <span>每页最多 {{ TASK_ENDPOINT_PAGE_SIZE }} 个接口</span>
                <button data-testid="task-endpoint-next" type="button" :disabled="taskEndpointPage >= taskEndpointPageCount" @click="taskEndpointPage += 1">下一页</button>
              </div>
            </section>
          </div>
        </template>
        <div v-else class="management-empty">
          <h2>选择左侧任务</h2>
          <p>可以查看任务范围、修改名称、执行任务或删除不再使用的任务。</p>
          <button class="primary-command" type="button" @click="createTaskInWorkbench">去工作台新建任务</button>
        </div>
      </main>
    </div>
  </section>
</template>
