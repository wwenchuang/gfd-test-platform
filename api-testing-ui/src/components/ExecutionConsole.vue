<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { RotateCw, Square } from 'lucide-vue-next'

import type { ExecutionCaseResult, ExecutionConnectionState, ExecutionEventView, ExecutionView } from '../api/contracts'
import { executionFailureBuckets, executionMetrics } from '../utils/executionPresentation'
import CaseEvidence from './CaseEvidence.vue'
import CaseResultList from './CaseResultList.vue'
import ExecutionLog from './ExecutionLog.vue'
import ExecutionOverview from './ExecutionOverview.vue'

const props = defineProps<{ executions: ExecutionView[]; active: ExecutionView | null; events: ExecutionEventView[]; connectionState: ExecutionConnectionState; loading?: boolean }>()
const emit = defineEmits<{
  select: [id: string]
  cancel: [id: string]
  rerun: [execution: ExecutionView]
  reconnect: [id: string]
  inspect: [result: ExecutionCaseResult]
  edit: [result: ExecutionCaseResult, execution: ExecutionView]
}>()
const tab = ref<'trace' | 'cases' | 'report'>('trace')
const selected = ref<ExecutionCaseResult | null>(null)
const running = computed(() => props.active && !['DONE', 'CANCELLED', 'PASSED', 'FAILED', 'BROKEN'].includes(props.active.state))
const caseLabels = computed(() => Object.fromEntries((props.active?.case_results || []).map(result => [result.execution_case_id, result.case_name || result.endpoint_summary || result.path])))
const metrics = computed(() => props.active ? executionMetrics(props.active) : null)
const buckets = computed(() => props.active ? executionFailureBuckets(props.active) : null)

watch(() => props.active?.id, () => {
  const active = props.active
  selected.value = active?.case_results[0] || null
  tab.value = 'trace'
}, { immediate: true })
watch(() => props.active?.case_results, results => {
  if (!results) return
  selected.value = results.find(item => item.execution_case_id === selected.value?.execution_case_id) || results[0] || null
})

function selectCase(result: ExecutionCaseResult): void {
  selected.value = result
  emit('inspect', result)
}
</script>

<template>
  <div class="execution-console">
    <aside class="execution-list panel">
      <header class="panel-header"><h2>执行记录</h2><span>{{ executions.length }} 条</span></header>
      <button v-for="execution in executions" :key="execution.id" type="button" :class="['execution-row', { active: execution.id === active?.id }]" @click="emit('select', execution.id)">
        <strong>{{ execution.execution_type === 'debug' ? '在线调试' : '自动回归' }}</strong><span>{{ execution.environment_name || '未命名环境' }}</span><small>{{ execution.created_at ? new Date(execution.created_at).toLocaleString('zh-CN') : '' }}</small><b>{{ execution.state }}</b>
      </button>
      <p v-if="!loading && !executions.length" class="state-message">还没有执行记录，可从工作台调试已保存草稿。</p>
    </aside>
    <main class="execution-main">
      <template v-if="active">
        <div class="execution-heading">
          <div><h2>{{ active.execution_type === 'debug' ? '在线调试' : '自动回归' }}</h2><span>{{ active.environment_name }} · {{ active.case_results.length }} 条用例</span></div>
          <div><button v-if="connectionState === 'failed'" class="secondary-command" type="button" @click="emit('reconnect', active.id)"><RotateCw :size="14" />重新连接日志</button><button v-if="running" class="secondary-command" type="button" @click="emit('cancel', active.id)"><Square :size="14" />取消</button><button v-else-if="active.case_results.some(item => ['FAILED','BROKEN'].includes(item.status))" class="secondary-command" type="button" @click="emit('rerun', active)"><RotateCw :size="14" />重跑失败项</button></div>
        </div>
        <ExecutionOverview :execution="active" />
        <nav class="execution-tabs" aria-label="执行详情视图">
          <button type="button" :class="{ active: tab === 'trace' }" @click="tab = 'trace'">实时轨迹</button>
          <button data-testid="execution-tab-cases" type="button" :class="{ active: tab === 'cases' }" @click="tab = 'cases'">用例明细</button>
          <button type="button" :class="{ active: tab === 'report' }" @click="tab = 'report'">测试报告</button>
        </nav>
        <div v-if="tab === 'trace'" class="execution-trace-grid">
          <section class="trace-case-panel panel"><header class="panel-header"><h2>用例进度</h2><span>{{ active.case_results.length }} 条</span></header><CaseResultList :results="active.case_results" :active-id="selected?.execution_case_id" row-test-id="realtime-case-row" @select="selectCase" /></section>
          <ExecutionLog :key="active.id" :events="events" :connection-state="connectionState" :case-labels="caseLabels" />
        </div>
        <div v-else-if="tab === 'cases'" class="execution-detail-grid embedded-evidence">
          <CaseResultList :results="active.case_results" :active-id="selected?.execution_case_id" @select="selected = $event" />
          <CaseEvidence v-if="selected" :result="selected" @edit="emit('edit', $event, active)" @rerun="emit('rerun', active)" />
        </div>
        <section v-else class="execution-report-preview">
          <header><div><span>本次执行</span><strong>{{ active.environment_name }}</strong></div><p>完整诊断报告保留真实用例状态，并按产品、脚本数据和环境问题归类。</p></header>
          <div v-if="metrics && buckets" class="report-preview-grid"><div><span>通过率</span><strong>{{ metrics.passRate }}%</strong></div><div><span>产品失败</span><strong>{{ buckets.product }}</strong></div><div><span>脚本/数据</span><strong>{{ buckets.scriptData }}</strong></div><div><span>环境异常</span><strong>{{ buckets.environment }}</strong></div></div>
          <CaseResultList :results="active.case_results" :active-id="selected?.execution_case_id" @select="selectCase" />
        </section>
      </template>
      <div v-else class="section-empty">选择一条执行记录查看实时日志和结果。</div>
    </main>
  </div>
</template>
