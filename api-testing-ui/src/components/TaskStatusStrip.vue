<script setup lang="ts">
import { computed } from 'vue'
import { FilePlus2, Pencil, Play, Save, Workflow } from 'lucide-vue-next'

import type { ApiTestTask } from '../api/contracts'

const props = withDefaults(defineProps<{
  task: ApiTestTask | null
  taskNameDraft?: string
  selectedCount: number
  scopeMatchesTask?: boolean
  environmentName?: string
  saving?: boolean
  running?: boolean
}>(), { taskNameDraft: '', environmentName: '未选择环境', saving: false, running: false, scopeMatchesTask: true })

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
      ? `可执行 ${task.runnable_baseline_count} 条用例`
      : '待采纳基线'
  }
  return stateLabels[task.state] || task.state
}
const runnableEndpointCount = (task: ApiTestTask | null): number => {
  if (!task) return 0
  return task.runnable_endpoint_count ?? Math.min(task.runnable_baseline_count, task.selected_endpoint_ids.length)
}
const taskTypeLabel = (task: ApiTestTask | null, selectedCount = 0): string => {
  if (!task) return selectedCount > 1 ? '多条任务' : selectedCount === 1 ? '单条任务' : '新任务'
  if (task.runnable_baseline_count > 0) return '基线'
  const count = task.selected_endpoint_ids.length || selectedCount
  return count > 1 ? '多条任务' : '单条任务'
}
const currentTaskType = computed(() => taskTypeLabel(props.task, props.selectedCount))
const nextStep = computed(() => {
  if (!props.selectedCount) return '先在左侧勾选接口，再生成用例或手工编辑。点击接口名称可查看详情。'
  if (!props.task) return '可在右侧生成用例，或点击接口名称手工编辑；生成时会自动保存任务。也可以先填写名称，将已选接口保存为任务，稍后从“任务管理”继续。'
  if (!props.scopeMatchesTask) return '接口选择已变化，请更新任务接口，平台将重新统计可执行基线；保存不会执行接口。'
  if (['designing', 'debugging', 'running'].includes(props.task.state)) return '当前任务正在处理，请查看生成进度或执行记录，完成后再进行下一步。'
  if (!props.task.runnable_baseline_count) return '生成或编辑用例 → 保存并调试 → 在调试结果中采纳为基线，才可执行本任务。已保存用例可在“用例管理”找回。'
  return '可以执行本任务；执行后自动进入“执行记录”，正式回归结果可在“测试报告”查看。只运行已采纳的基线，不包含其他草稿。'
})

function updateName(event: Event): void {
  emit('update:taskNameDraft', (event.target as HTMLInputElement).value)
}
</script>

<template>
  <section :class="['task-status-strip', { 'task-status-draft': !task }]" aria-label="当前测试任务">
    <template v-if="!task && !selectedCount">
      <div class="task-start-heading"><Workflow :size="18" /><strong>从一个接口开始</strong><span>不必先建任务，选好接口就能设计用例。</span></div>
      <ol class="task-start-steps" aria-label="接口测试流程">
        <li><strong>1 选接口</strong><span>点名称看详情，勾选加入任务</span></li>
        <li><strong>2 设计用例</strong><span>AI 生成或手工编辑</span></li>
        <li><strong>3 保存并调试</strong><span>检查实际响应与断言</span></li>
        <li><strong>4 采纳基线后回归</strong><span>执行记录、报告中查看结果</span></li>
      </ol>
    </template>
    <template v-else>
    <div class="task-heading">
      <Workflow :size="18" />
      <div>
        <span>{{ task ? '当前任务' : '准备设计用例' }}</span>
        <strong :title="task?.name || '尚未保存为任务'">{{ task?.name || `已选 ${selectedCount} 个接口 · 尚未保存` }}</strong>
      </div>
      <em class="task-type-chip">{{ currentTaskType }}</em>
    </div>
    <template v-if="task">
    <div class="task-fact"><span>任务接口</span><strong>{{ `已保存 ${task.selected_endpoint_ids.length} 个接口` }}</strong><small>当前选择 {{ selectedCount }} 个</small></div>
    <div class="task-fact"><span>环境</span><strong>{{ environmentName }}</strong></div>
    <div class="task-fact"><span>执行用例 · 已采纳基线</span><strong>{{ task && !scopeMatchesTask ? '待重新统计' : task ? `${task.runnable_baseline_count} 条` : '0 条' }}</strong><small v-if="scopeMatchesTask">覆盖 {{ runnableEndpointCount(task) }} 个接口</small><small v-if="scopeMatchesTask && task && task.runnable_baseline_count > runnableEndpointCount(task)" class="task-count-warning">同一接口存在多版本基线</small></div>
    <div class="task-fact"><span>状态</span><strong :class="task ? `task-state-${task.state}` : ''">{{ task && !scopeMatchesTask ? '范围已调整，待保存' : runnableLabel(task) }}</strong></div>
    </template>
    <div class="task-actions">
      <button data-testid="new-task" class="secondary-command" type="button" :disabled="saving || running" @click="$emit('new')"><FilePlus2 :size="15" />新建任务</button>
      <button data-testid="save-task" class="secondary-command" type="button" :disabled="saving || running || !selectedCount" @click="$emit('save')"><Save :size="15" />{{ saving ? '保存中' : task ? '更新任务接口' : '保存为任务' }}</button>
      <button v-if="task" data-testid="run-task" class="primary-command" type="button" :title="nextStep" :disabled="running || !scopeMatchesTask || !selectedCount || task.runnable_baseline_count < 1 || ['designing','debugging','running'].includes(task.state)" @click="$emit('run')"><Play :size="15" />{{ running ? '创建执行中' : '执行本任务' }}</button>
    </div>
    <div class="task-management">
      <label>任务名称
        <input data-testid="task-name-input" :value="taskNameDraft" maxlength="200" placeholder="例如：收藏链路发版回归" @input="updateName" />
      </label>
      <button v-if="task" data-testid="rename-task" class="secondary-command" type="button" :disabled="saving || running" @click="emit('rename-task')"><Pencil :size="15" />保存名称</button>
    </div>
    </template>
    <p class="task-help" role="status" data-testid="task-next-step"><strong>下一步：</strong>{{ nextStep }}</p>
  </section>
</template>
