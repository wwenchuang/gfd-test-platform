<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { Edit3, Play, Save, Trash2 } from 'lucide-vue-next'
import { useRouter } from 'vue-router'

import type { ApiEndpoint, ApiTestTask } from '../api/contracts'
import ContextBar from '../components/ContextBar.vue'
import TaskListPanel from '../components/TaskListPanel.vue'
import { useAssetsStore } from '../stores/assets'
import { useContextStore } from '../stores/context'
import { useTasksStore } from '../stores/tasks'
import { compareGroupNames, endpointGroupName } from '../utils/endpointGroups'

const context = useContextStore()
const assets = useAssetsStore()
const tasks = useTasksStore()
const router = useRouter()

const localError = ref('')
const taskNameDraft = ref('')

const activeTask = computed(() => tasks.task)
const taskEnvironmentNames = computed(() => Object.fromEntries(
  context.environmentRevisions.map(item => [item.id, `${item.name} · v${item.revision}`]),
))
const selectedEndpoints = computed(() => {
  const selected = new Set(activeTask.value?.selected_endpoint_ids || [])
  return assets.endpoints.filter(endpoint => selected.has(endpoint.id))
})
const groupedSelectedEndpoints = computed(() => {
  const grouped = new Map<string, ApiEndpoint[]>()
  for (const endpoint of selectedEndpoints.value) {
    const group = endpointGroupName(endpoint)
    grouped.set(group, [...(grouped.get(group) || []), endpoint])
  }
  return [...grouped.entries()].sort(([left], [right]) => compareGroupNames(left, right))
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
    await tasks.list(context.projectId)
    const restored = await tasks.restore(context.projectId)
    if (restored) await selectTask(restored.id)
  }
  if (!tasks.task && context.sourceRevisionId) await assets.load(context.sourceRevisionId)
})

watch(() => tasks.task?.name, name => {
  taskNameDraft.value = name || ''
}, { immediate: true })

function changeProject(projectId: string | null): void {
  context.selectProject(projectId)
  tasks.clear()
  tasks.tasks = []
  assets.endpoints = []
  localError.value = ''
  if (projectId) void tasks.list(projectId)
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

async function selectTask(taskId: string): Promise<void> {
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
  const environmentRevisionId = context.environmentRevisionId || tasks.task.environment_revision_id
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
    await tasks.remove(task.id)
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
  await router.push({ name: 'workbench' })
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
        <p class="eyebrow">API TASK MANAGEMENT</p>
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
    <p v-if="context.error || tasks.error || assets.error || localError" class="inline-error">{{ context.error || tasks.error || assets.error || localError }}</p>
    <div class="management-shell task-management-shell" data-testid="task-management-shell">
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
            <div>
              <p>当前选中任务</p>
              <h2 data-testid="selected-task-title">{{ activeTask.name }}</h2>
            </div>
            <span :class="`task-state-pill task-state-${activeTask.state}`">{{ activeTask.state }}</span>
          </header>
          <div class="management-detail-body">
            <div class="task-detail-form">
              <label>任务名称
                <input v-model="taskNameDraft" data-testid="task-detail-name" />
              </label>
              <div class="detail-action-row">
                <button class="secondary-command" type="button" :disabled="tasks.saving" @click="renameTask"><Save :size="15" />保存名称</button>
                <button class="secondary-command" type="button" @click="editTaskInWorkbench"><Edit3 :size="15" />编辑范围</button>
                <button data-testid="task-detail-run" class="primary-command" type="button" :disabled="tasks.running || activeTask.runnable_baseline_count <= 0" @click="runTask()">
                  <Play :size="15" />执行任务
                </button>
                <button data-testid="task-detail-delete" class="secondary-command danger-command" type="button" :disabled="tasks.saving || tasks.running" @click="deleteTask(activeTask)">
                  <Trash2 :size="15" />删除
                </button>
              </div>
            </div>
            <section class="management-summary-grid" aria-label="任务概要">
              <div><span>接口版本</span><strong>{{ sourceLabel }}</strong></div>
              <div><span>执行环境</span><strong>{{ environmentLabel }}</strong></div>
              <div><span>接口数量</span><strong>{{ activeTask.selected_endpoint_ids.length }}</strong></div>
              <div><span>可执行基线</span><strong>{{ activeTask.runnable_baseline_count }}</strong></div>
            </section>
            <section class="task-endpoint-section">
              <header><h3>任务接口范围</h3><span>{{ selectedEndpoints.length }} / {{ activeTask.selected_endpoint_ids.length }}</span></header>
              <div class="task-endpoint-list">
                <template v-for="[group, endpoints] in groupedSelectedEndpoints" :key="group">
                  <h4>{{ group }} <span>{{ endpoints.length }}</span></h4>
                  <article v-for="endpoint in endpoints" :key="endpoint.id" class="task-endpoint-row">
                    <span :class="['method-badge', `method-${endpoint.method.toLowerCase()}`]">{{ endpoint.method }}</span>
                    <div><strong>{{ endpoint.summary || endpoint.path }}</strong><code>{{ endpoint.path }}</code></div>
                  </article>
                </template>
                <p v-if="!selectedEndpoints.length" class="section-empty">{{ assets.state === 'loading' ? '正在读取任务接口...' : '当前接口版本里未找到该任务保存的接口。' }}</p>
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
