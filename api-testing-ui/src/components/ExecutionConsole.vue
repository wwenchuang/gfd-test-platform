<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { RotateCw, Search, Square, Trash2 } from 'lucide-vue-next'

import type { ExecutionCaseResult, ExecutionConnectionState, ExecutionEventView, ExecutionView } from '../api/contracts'
import { executionConclusion, executionFailureBuckets, executionMetrics, executionScopeLabel, executionSourceScope, executionTypeLabel } from '../utils/executionPresentation'
import CaseEvidence from './CaseEvidence.vue'
import CaseResultList from './CaseResultList.vue'
import ExecutionLog from './ExecutionLog.vue'
import ExecutionOverview from './ExecutionOverview.vue'

const props = defineProps<{ executions: ExecutionView[]; active: ExecutionView | null; events: ExecutionEventView[]; connectionState: ExecutionConnectionState; loading?: boolean; endpointId?: string }>()
const emit = defineEmits<{
  select: [id: string]
  cancel: [id: string]
  rerun: [execution: ExecutionView]
  reconnect: [id: string]
  inspect: [result: ExecutionCaseResult]
  edit: [result: ExecutionCaseResult, execution: ExecutionView]
  delete: [id: string]
  deleteMany: [ids: string[]]
  clearEndpointFilter: []
}>()
const tab = ref<'trace' | 'cases' | 'report'>('trace')
const selected = ref<ExecutionCaseResult | null>(null)
const selectedExecutionIds = ref<Set<string>>(new Set())
const executionSearch = ref('')
const sourceFilter = ref<'all' | 'formal' | 'debug'>('all')
const conclusionFilter = ref<'all' | 'passed' | 'problem' | 'running'>('all')
const executionPage = ref(1)
const EXECUTION_PAGE_SIZE = 20
const terminalStates = new Set(['DONE', 'CANCELLED', 'PASSED', 'FAILED', 'BROKEN'])
const running = computed(() => props.active && !terminalStates.has(props.active.state))
const canRerunActive = computed(() => Boolean(props.active && terminalStates.has(props.active.state) && props.active.case_results.length))
const caseLabels = computed(() => Object.fromEntries((props.active?.case_results || []).map(result => [result.execution_case_id, result.case_name || result.endpoint_summary || result.path])))
const metrics = computed(() => props.active ? executionMetrics(props.active) : null)
const buckets = computed(() => props.active ? executionFailureBuckets(props.active) : null)
const selectedExecutionCount = computed(() => selectedExecutionIds.value.size)
const visibleExecutions = computed(() => {
  const keyword = executionSearch.value.trim().toLocaleLowerCase()
  return props.executions.filter(execution => {
    if (props.endpointId && !execution.case_results.some(result => result.endpoint_id === props.endpointId)) return false
    if (sourceFilter.value !== 'all' && executionSourceScope(execution) !== sourceFilter.value) return false
    const conclusion = executionConclusion(execution)
    if (conclusionFilter.value === 'passed' && conclusion.tone !== 'passed') return false
    if (conclusionFilter.value === 'running' && conclusion.tone !== 'running') return false
    if (conclusionFilter.value === 'problem' && ['passed', 'running'].includes(conclusion.tone)) return false
    if (!keyword) return true
    return [
      executionDisplayName(execution), executionTypeLabel(execution), executionScopeLabel(execution), execution.environment_name, execution.id,
      ...execution.case_results.flatMap(result => [result.case_name, result.endpoint_summary, result.method, result.path]),
    ]
      .join(' ')
      .toLocaleLowerCase()
      .includes(keyword)
  })
})
const executionPageCount = computed(() => Math.max(1, Math.ceil(visibleExecutions.value.length / EXECUTION_PAGE_SIZE)))
const pagedExecutions = computed(() => {
  const start = (executionPage.value - 1) * EXECUTION_PAGE_SIZE
  return visibleExecutions.value.slice(start, start + EXECUTION_PAGE_SIZE)
})
const allVisibleSelected = computed(() => visibleExecutions.value.length > 0 && visibleExecutions.value.every(item => selectedExecutionIds.value.has(item.id)))

watch([executionSearch, sourceFilter, conclusionFilter], () => {
  executionPage.value = 1
})
watch(executionPageCount, pageCount => {
  if (executionPage.value > pageCount) executionPage.value = pageCount
})

watch(() => props.active?.id, () => {
  const active = props.active
  selected.value = active?.case_results[0] || null
  tab.value = 'trace'
}, { immediate: true })
watch(() => props.active?.case_results, results => {
  if (!results) return
  selected.value = results.find(item => item.execution_case_id === selected.value?.execution_case_id) || results[0] || null
})
watch(() => props.executions.map(item => item.id).join('|'), () => {
  const visible = new Set(props.executions.map(item => item.id))
  selectedExecutionIds.value = new Set([...selectedExecutionIds.value].filter(id => visible.has(id)))
})

function selectCase(result: ExecutionCaseResult): void {
  selected.value = result
  emit('inspect', result)
}

function toggleExecution(id: string): void {
  const next = new Set(selectedExecutionIds.value)
  if (next.has(id)) next.delete(id)
  else next.add(id)
  selectedExecutionIds.value = next
}

function toggleAllExecutions(): void {
  const next = new Set(selectedExecutionIds.value)
  if (allVisibleSelected.value) visibleExecutions.value.forEach(item => next.delete(item.id))
  else visibleExecutions.value.forEach(item => next.add(item.id))
  selectedExecutionIds.value = next
}

function deleteSelected(): void {
  emit('deleteMany', [...selectedExecutionIds.value])
  selectedExecutionIds.value = new Set()
}

function executionResultCount(execution: ExecutionView): number {
  return execution.case_results.length || Number(execution.summary?.TOTAL || execution.summary?.total || 0)
}

function executionTaskType(execution: ExecutionView): string {
  if (execution.execution_type === 'baseline_regression') return '基线'
  return executionResultCount(execution) > 1 ? '多条' : '单条'
}

function executionDisplayName(execution: ExecutionView): string {
  return execution.task_name || executionTypeLabel(execution)
}

function executionSubtitle(execution: ExecutionView): string {
  const parts = [
    executionScopeLabel(execution),
    executionTypeLabel(execution),
    `${executionResultCount(execution)} 条`,
    execution.environment_name || '未命名环境',
  ]
  return parts.join(' · ')
}

function executionRowConclusion(execution: ExecutionView): { label: string; tone: string } {
  return executionConclusion(execution)
}
</script>

<template>
  <div class="execution-console">
    <aside class="execution-list panel">
      <header class="panel-header">
        <h2>执行记录</h2><span>{{ visibleExecutions.length }}/{{ executions.length }} 条</span>
      </header>
      <div class="execution-filter-tools">
        <label class="search-box"><Search :size="14" /><span class="sr-only">搜索执行记录</span><input v-model="executionSearch" data-testid="execution-filter-search" placeholder="搜索任务或环境" /></label>
        <select v-model="sourceFilter" data-testid="execution-filter-source" aria-label="执行来源">
          <option value="all">全部来源</option><option value="formal">正式回归</option><option value="debug">在线调试</option>
        </select>
        <select v-model="conclusionFilter" data-testid="execution-filter-conclusion" aria-label="执行结论">
          <option value="all">全部结论</option><option value="passed">通过</option><option value="problem">有问题</option><option value="running">执行中</option>
        </select>
      </div>
      <div v-if="endpointId" class="execution-scope-filter"><span>当前仅显示所选接口参与的执行</span><button data-testid="execution-clear-endpoint-filter" type="button" class="text-command" @click="emit('clearEndpointFilter')">清除接口筛选</button></div>
      <div class="execution-list-tools">
        <button type="button" class="text-command" :disabled="!visibleExecutions.length" @click="toggleAllExecutions">{{ allVisibleSelected ? '取消当前筛选' : '全选当前筛选' }}</button>
        <button type="button" class="danger-command" :disabled="!selectedExecutionCount" @click="deleteSelected"><Trash2 :size="13" />删除 {{ selectedExecutionCount || '' }}</button>
      </div>
      <article
        v-for="execution in pagedExecutions"
        :key="execution.id"
        :data-testid="`execution-row-${execution.id}`"
        role="button"
        tabindex="0"
        :class="['execution-row', { active: execution.id === active?.id }]"
        @click="emit('select', execution.id)"
        @keydown.enter="emit('select', execution.id)"
      >
        <input type="checkbox" :checked="selectedExecutionIds.has(execution.id)" aria-label="选择执行记录" @click.stop="toggleExecution(execution.id)" />
        <span class="execution-row-body">
          <strong :title="executionDisplayName(execution)">{{ executionDisplayName(execution) }}</strong>
          <span><em class="execution-type-chip">{{ executionTaskType(execution) }}</em>{{ executionSubtitle(execution) }}</span>
          <small>{{ execution.created_at ? new Date(execution.created_at).toLocaleString('zh-CN') : '' }}</small>
        </span>
        <b :class="`tone-${executionRowConclusion(execution).tone}`">{{ executionRowConclusion(execution).label }}</b>
        <button type="button" class="icon-danger" aria-label="删除执行记录" @click.stop="emit('delete', execution.id)"><Trash2 :size="13" /></button>
      </article>
      <nav v-if="executionPageCount > 1" class="list-pagination" aria-label="执行记录分页">
        <button type="button" :disabled="executionPage === 1" @click="executionPage -= 1">上一页</button>
        <span>第 {{ executionPage }} / {{ executionPageCount }} 页</span>
        <button data-testid="execution-page-next" type="button" :disabled="executionPage === executionPageCount" @click="executionPage += 1">下一页</button>
      </nav>
      <p v-if="!loading && !visibleExecutions.length" class="state-message">{{ executions.length ? '当前筛选下没有匹配执行记录。' : '还没有执行记录，可从工作台调试已保存草稿。' }}</p>
    </aside>
    <main class="execution-main">
      <template v-if="active">
        <div class="execution-heading">
          <div><h2 :title="executionDisplayName(active)">{{ executionDisplayName(active) }}</h2><span><em class="execution-type-chip">{{ executionTaskType(active) }}</em>{{ executionSubtitle(active) }}</span></div>
          <div><button v-if="connectionState === 'failed'" class="secondary-command" type="button" @click="emit('reconnect', active.id)"><RotateCw :size="14" />重新连接日志</button><button v-if="running" class="secondary-command" type="button" @click="emit('cancel', active.id)"><Square :size="14" />取消</button><button v-else-if="canRerunActive" data-testid="rerun-active-execution" class="primary-command" type="button" @click="emit('rerun', active)"><RotateCw :size="14" />重新执行此记录</button></div>
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
      <div v-else class="section-empty">{{ loading ? '正在读取本次执行，请稍候…' : '选择一条执行记录查看实时日志和结果。' }}</div>
    </main>
  </div>
</template>
