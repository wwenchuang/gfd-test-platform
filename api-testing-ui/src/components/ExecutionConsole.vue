<script setup lang="ts">
import { computed } from 'vue'
import { RotateCw, Square } from 'lucide-vue-next'

import type { ExecutionConnectionState, ExecutionEventView, ExecutionView } from '../api/contracts'
import ExecutionLog from './ExecutionLog.vue'

const props = defineProps<{ executions: ExecutionView[]; active: ExecutionView | null; events: ExecutionEventView[]; connectionState: ExecutionConnectionState; loading?: boolean }>()
const emit = defineEmits<{ select: [id: string]; cancel: [id: string]; rerun: [execution: ExecutionView]; reconnect: [id: string] }>()
const running = computed(() => props.active && !['DONE', 'CANCELLED', 'PASSED', 'FAILED', 'BROKEN'].includes(props.active.state))
</script>

<template>
  <div class="execution-console">
    <aside class="execution-list panel">
      <header class="panel-header"><h2>执行记录</h2><span>{{ executions.length }} 条</span></header>
      <button
        v-for="execution in executions"
        :key="execution.id"
        type="button"
        :class="['execution-row', { active: execution.id === active?.id }]"
        @click="emit('select', execution.id)"
      >
        <strong>{{ execution.execution_type === 'debug' ? '在线调试' : '自动回归' }}</strong>
        <span>{{ execution.environment_name || '未命名环境' }}</span>
        <small>{{ execution.created_at ? new Date(execution.created_at).toLocaleString('zh-CN') : '' }}</small>
        <b>{{ execution.state }}</b>
      </button>
      <p v-if="!loading && !executions.length" class="state-message">还没有执行记录，可从工作台调试已保存草稿。</p>
    </aside>
    <main class="execution-main">
      <div v-if="active" class="execution-heading">
        <div>
          <h2>{{ active.execution_type === 'debug' ? '在线调试' : '自动回归' }}</h2>
          <span>{{ active.environment_name }} · {{ active.case_results.length }} 条用例</span>
        </div>
        <div>
          <button v-if="connectionState === 'failed'" class="secondary-command" type="button" @click="emit('reconnect', active.id)"><RotateCw :size="14" />重新连接日志</button>
          <button v-if="running" class="secondary-command" type="button" @click="emit('cancel', active.id)"><Square :size="14" />取消</button>
          <button v-else-if="active.case_results.some(item => ['FAILED','BROKEN'].includes(item.status))" class="secondary-command" type="button" @click="emit('rerun', active)"><RotateCw :size="14" />重跑失败项</button>
        </div>
      </div>
      <ExecutionLog v-if="active" :events="events" :connection-state="connectionState" />
      <div v-else class="section-empty">选择一条执行记录查看实时日志和结果。</div>
    </main>
  </div>
</template>
