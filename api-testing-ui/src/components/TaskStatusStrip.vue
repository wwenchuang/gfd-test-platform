<script setup lang="ts">
import { computed } from 'vue'
import { FilePlus2, Pencil, Play, Save, Workflow } from 'lucide-vue-next'

import type { ApiTestTask } from '../api/contracts'

const props = withDefaults(defineProps<{
  task: ApiTestTask | null
  taskNameDraft?: string
  selectedCount: number
  environmentName?: string
  saving?: boolean
  running?: boolean
}>(), { taskNameDraft: '', environmentName: '未选择环境', saving: false, running: false })

const emit = defineEmits<{
  save: []
  run: []
  new: []
  'rename-task': []
  'update:taskNameDraft': [name: string]
}>()

const stateLabels: Record<string, string> = {
  draft: '待设计', designing: 'AI 生成中', debugging: '调试中', ready: '可执行',
  running: '执行中', failed: '需要处理', completed: '已完成',
}
const runnableLabel = (task: ApiTestTask | null): string => {
  if (!task) return '待创建'
  if (task.state === 'ready') {
    return task.runnable_baseline_count > 0
      ? `可执行 ${task.runnable_baseline_count} / ${task.selected_endpoint_ids.length}`
      : '待采纳基线'
  }
  return stateLabels[task.state] || task.state
}
const taskTypeLabel = (task: ApiTestTask | null, selectedCount = 0): string => {
  if (!task) return selectedCount > 1 ? '多条任务' : selectedCount === 1 ? '单条任务' : '新任务'
  if (task.runnable_baseline_count > 0) return '基线'
  const count = task.selected_endpoint_ids.length || selectedCount
  return count > 1 ? '多条任务' : '单条任务'
}
const currentTaskType = computed(() => taskTypeLabel(props.task, props.selectedCount))

function updateName(event: Event): void {
  emit('update:taskNameDraft', (event.target as HTMLInputElement).value)
}
</script>

<template>
  <section class="task-status-strip" aria-label="当前测试任务">
    <div class="task-heading">
      <Workflow :size="18" />
      <div>
        <span>当前任务</span>
        <strong :title="task?.name || taskNameDraft || '新任务未保存'">{{ task?.name || taskNameDraft || '新任务未保存' }}</strong>
      </div>
      <em class="task-type-chip">{{ currentTaskType }}</em>
    </div>
    <div class="task-fact"><span>范围</span><strong>{{ task ? `已保存 ${task.selected_endpoint_ids.length} 个接口` : '未保存' }}</strong><small>当前选择 {{ selectedCount }} 个</small></div>
    <div class="task-fact"><span>环境</span><strong>{{ environmentName }}</strong></div>
    <div class="task-fact"><span>基线</span><strong>{{ task ? `${task.runnable_baseline_count} / ${task.selected_endpoint_ids.length}` : '0 / 0' }}</strong><small>可加入定时回归</small></div>
    <div class="task-fact"><span>状态</span><strong :class="task ? `task-state-${task.state}` : ''">{{ runnableLabel(task) }}</strong></div>
    <div class="task-actions">
      <button data-testid="new-task" class="secondary-command" type="button" :disabled="saving || running" @click="$emit('new')"><FilePlus2 :size="15" />新建任务</button>
      <button data-testid="save-task" class="secondary-command" type="button" :disabled="saving || !selectedCount" @click="$emit('save')"><Save :size="15" />{{ saving ? '保存中' : '保存任务范围' }}</button>
      <button data-testid="run-task" class="primary-command" type="button" :disabled="running || !task || task.runnable_baseline_count < 1 || ['designing','debugging','running'].includes(task.state)" @click="$emit('run')"><Play :size="15" />{{ running ? '创建执行中' : '执行本任务' }}</button>
    </div>
    <div class="task-management">
      <label>任务名称
        <input data-testid="task-name-input" :value="taskNameDraft" maxlength="200" placeholder="例如：收藏链路发版回归" @input="updateName" />
      </label>
      <button data-testid="rename-task" class="secondary-command" type="button" :disabled="saving || !task" @click="emit('rename-task')"><Pencil :size="15" />保存名称</button>
    </div>
    <p class="task-help">任务保存当前接口范围；调试通过后采纳为基线，后续可作为发版定时回归的执行集合。</p>
  </section>
</template>
