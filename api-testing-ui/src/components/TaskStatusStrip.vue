<script setup lang="ts">
import { Play, Save, Workflow } from 'lucide-vue-next'

import type { ApiTestTask } from '../api/contracts'

const props = withDefaults(defineProps<{
  task: ApiTestTask | null
  selectedCount: number
  environmentName?: string
  saving?: boolean
  running?: boolean
}>(), { environmentName: '未选择环境', saving: false, running: false })

defineEmits<{ save: []; run: [] }>()

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
</script>

<template>
  <section class="task-status-strip" aria-label="当前测试任务">
    <div class="task-heading">
      <Workflow :size="18" />
      <div><span>当前任务</span><strong>{{ task?.name || '尚未保存测试任务' }}</strong></div>
    </div>
    <div class="task-fact"><span>范围</span><strong>{{ task ? `已保存 ${task.selected_endpoint_ids.length} 个接口` : '未保存' }}</strong><small>当前选择 {{ selectedCount }} 个</small></div>
    <div class="task-fact"><span>环境</span><strong>{{ environmentName }}</strong></div>
    <div class="task-fact"><span>状态</span><strong :class="task ? `task-state-${task.state}` : ''">{{ runnableLabel(task) }}</strong></div>
    <div class="task-actions">
      <button data-testid="save-task" class="secondary-command" type="button" :disabled="saving || !selectedCount" @click="$emit('save')"><Save :size="15" />{{ saving ? '保存中' : '保存本次任务' }}</button>
      <button data-testid="run-task" class="primary-command" type="button" :disabled="running || !task || task.runnable_baseline_count < 1 || ['designing','debugging','running'].includes(task.state)" @click="$emit('run')"><Play :size="15" />{{ running ? '创建执行中' : '执行本任务' }}</button>
    </div>
  </section>
</template>
