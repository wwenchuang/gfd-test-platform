<script setup lang="ts">
import { computed, ref } from 'vue'
import { FilePlus2, Pencil, Play, Search, Trash2 } from 'lucide-vue-next'

import type { ApiTestTask } from '../api/contracts'
import { compactDateTime, taskLatestResult, taskStateLabel } from '../utils/taskPresentation'

const props = withDefaults(defineProps<{
  tasks: ApiTestTask[]
  activeTaskId?: string
  environmentNames?: Record<string, string>
  loading?: boolean
  saving?: boolean
  running?: boolean
}>(), { activeTaskId: '', environmentNames: () => ({}), loading: false, saving: false, running: false })

const emit = defineEmits<{
  new: []
  select: [taskId: string]
  run: [taskId: string]
  delete: [task: ApiTestTask]
}>()

const query = ref('')
const filteredTasks = computed(() => {
  const keyword = query.value.trim().toLocaleLowerCase()
  if (!keyword) return props.tasks
  return props.tasks.filter(task => [
    task.name,
    task.state,
    task.source_revision_id,
    task.environment_revision_id,
    `${task.selected_endpoint_ids.length}`,
  ].join(' ').toLocaleLowerCase().includes(keyword))
})

function canRun(task: ApiTestTask): boolean {
  return task.runnable_baseline_count > 0 && !['designing', 'debugging', 'running'].includes(task.state)
}

function runnableEndpointCount(task: ApiTestTask): number {
  return task.runnable_endpoint_count ?? Math.min(task.runnable_baseline_count, task.selected_endpoint_ids.length)
}

function hasMultipleBaselineVersions(task: ApiTestTask): boolean {
  return task.runnable_baseline_count > runnableEndpointCount(task)
}

function environmentName(task: ApiTestTask): string {
  return props.environmentNames[task.environment_revision_id] || '任务保存环境'
}
</script>

<template>
  <aside class="task-list-panel" aria-label="任务列表">
    <header class="panel-header">
      <h2>任务列表</h2>
      <span>{{ tasks.length }}</span>
    </header>
    <div class="task-list-tools">
      <label class="search-box task-list-search">
        <Search :size="15" />
        <span class="sr-only">搜索任务</span>
        <input v-model="query" data-testid="task-list-search" placeholder="搜索任务名称" />
      </label>
      <button data-testid="task-list-new" class="secondary-command wide" type="button" :disabled="saving || running" @click="emit('new')">
        <FilePlus2 :size="15" />新建任务
      </button>
    </div>
    <div class="task-list-scroll">
      <button
        v-for="task in filteredTasks"
        :key="task.id"
        :data-testid="`task-list-item-${task.id}`"
        type="button"
        class="task-list-item"
        :class="{ active: task.id === activeTaskId }"
        @click="emit('select', task.id)"
      >
        <span class="task-list-main">
          <strong :title="task.name">{{ task.name }}</strong>
          <small>范围 {{ task.selected_endpoint_ids.length }} 个接口 · 执行 {{ task.runnable_baseline_count }} 条用例</small>
          <small>覆盖 {{ runnableEndpointCount(task) }} 个接口{{ hasMultipleBaselineVersions(task) ? '，含多版本基线' : '' }}</small>
          <small>环境 {{ environmentName(task) }}</small>
          <small>{{ taskLatestResult(task) }}</small>
          <small>更新 {{ compactDateTime(task.updated_at) }}</small>
        </span>
        <em :class="`task-state-${task.state}`">{{ taskStateLabel(task.state, task.runnable_baseline_count) }}</em>
        <span class="task-list-actions" @click.stop>
          <button :data-testid="`task-list-edit-${task.id}`" class="mini-icon" type="button" title="编辑任务" @click="emit('select', task.id)">
            <Pencil :size="14" />
          </button>
          <button :data-testid="`task-list-run-${task.id}`" class="mini-icon" type="button" title="执行任务" :disabled="running || !canRun(task)" @click="emit('run', task.id)">
            <Play :size="14" />
          </button>
          <button :data-testid="`task-list-delete-${task.id}`" class="mini-icon danger" type="button" title="删除任务" :disabled="saving || running" @click="emit('delete', task)">
            <Trash2 :size="14" />
          </button>
        </span>
      </button>
      <p v-if="loading" class="section-empty">正在读取任务...</p>
      <p v-else-if="!filteredTasks.length" class="section-empty">{{ tasks.length ? '没有匹配的任务。' : '暂无已保存任务。' }}</p>
    </div>
  </aside>
</template>
