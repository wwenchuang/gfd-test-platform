<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { ArrowLeft, Pencil, RotateCw } from 'lucide-vue-next'

import type { ExecutionCaseResult, ExecutionView } from '../api/contracts'
import { caseResultSummary, executionFailureBuckets, redactSensitiveEvidence } from '../utils/executionPresentation'
import CaseEvidence from './CaseEvidence.vue'
import CaseResultList from './CaseResultList.vue'
import ExecutionOverview from './ExecutionOverview.vue'

const props = defineProps<{ execution: ExecutionView }>()
const emit = defineEmits<{ back: []; edit: [result: ExecutionCaseResult, execution: ExecutionView]; rerun: [execution: ExecutionView] }>()
const selected = ref<ExecutionCaseResult | null>(props.execution.case_results[0] || null)
const filter = ref<'all' | 'failed' | 'broken' | 'skipped'>('all')
const buckets = computed(() => executionFailureBuckets(props.execution))
const filteredResults = computed(() => filter.value === 'all'
  ? props.execution.case_results
  : props.execution.case_results.filter(result => result.status === filter.value.toUpperCase()))
const diagnoses = computed(() => props.execution.case_results.filter(result => result.failure_analysis))
const technicalLog = computed(() => redactSensitiveEvidence(props.execution.case_results.map(result => ({
  case: result.case_name || result.path,
  endpoint: `${result.method} ${result.path}`,
  status: result.status,
  category: result.failure_category,
  duration_ms: result.duration_ms,
  summary: caseResultSummary(result),
  trace: result.sanitized_result.trace || result.sanitized_result.logs || [],
}))))

watch(() => props.execution, execution => { selected.value = execution.case_results[0] || null })
watch(filteredResults, results => {
  if (!results.some(result => result.execution_case_id === selected.value?.execution_case_id)) selected.value = results[0] || null
})
</script>

<template>
  <article class="diagnostic-report">
    <header class="diagnostic-report-header"><div><button class="secondary-command" type="button" @click="emit('back')"><ArrowLeft :size="14" />返回报告列表</button><span>生成于 {{ new Date(execution.created_at).toLocaleString('zh-CN') }}</span></div><div><button v-if="selected" class="secondary-command" type="button" @click="emit('edit', selected, execution)"><Pencil :size="14" />编辑当前用例</button><button v-if="execution.case_results.some(item => ['FAILED','BROKEN'].includes(item.status))" class="primary-command" type="button" @click="emit('rerun', execution)"><RotateCw :size="14" />重跑失败项</button></div></header>
    <section class="report-section"><div class="section-title"><span>01</span><div><h2>诊断结论</h2><p>状态来自真实执行，AI 仅补充失败解释。</p></div></div><ExecutionOverview :execution="execution" /></section>
    <section class="report-section"><div class="section-title"><span>02</span><div><h2>问题分布</h2><p>产品断言、脚本数据和环境问题分开统计。</p></div></div><div class="failure-bucket-grid"><div><strong>{{ buckets.product }}</strong><span>产品失败</span></div><div><strong>{{ buckets.scriptData }}</strong><span>脚本/数据</span></div><div><strong>{{ buckets.environment }}</strong><span>环境异常</span></div><div><strong>{{ buckets.skipped }}</strong><span>依赖跳过</span></div><div><strong>{{ buckets.cancelled }}</strong><span>已取消</span></div></div></section>
    <section class="report-section"><div class="section-title"><span>03</span><div><h2>AI 诊断摘要</h2><p>仅展示已有分析，缺失时保留平台确定性分类。</p></div></div><div v-if="diagnoses.length" class="diagnosis-list"><div v-for="result in diagnoses" :key="result.execution_case_id"><strong>{{ result.case_name }}</strong><span>{{ result.failure_analysis?.model }}</span><p>{{ result.failure_analysis?.analysis.summary }}</p><small>{{ result.failure_analysis?.analysis.root_cause }}</small></div></div><p v-else class="state-message">本次没有可用的 AI 失败分析。</p></section>
    <section class="report-section report-case-section"><div class="section-title"><span>04</span><div><h2>用例明细</h2><p>选择用例查看请求、响应、断言与执行轨迹。</p></div></div><div class="report-case-filters" aria-label="用例状态筛选"><button type="button" :class="{ active: filter === 'all' }" @click="filter = 'all'">全部</button><button type="button" :class="{ active: filter === 'failed' }" @click="filter = 'failed'">失败</button><button data-testid="report-filter-broken" type="button" :class="{ active: filter === 'broken' }" @click="filter = 'broken'">异常</button><button type="button" :class="{ active: filter === 'skipped' }" @click="filter = 'skipped'">跳过</button></div><div class="diagnostic-case-grid"><CaseResultList :results="filteredResults" :active-id="selected?.execution_case_id" row-test-id="report-case-row" @select="selected = $event" /><div><p v-if="selected" class="case-conclusion">{{ caseResultSummary(selected) }}</p><CaseEvidence v-if="selected" :result="selected" @edit="emit('edit', $event, execution)" @rerun="emit('rerun', execution)" /><p v-else class="state-message">当前筛选没有用例</p></div></div></section>
    <details class="technical-log"><summary>技术日志</summary><pre>{{ JSON.stringify(technicalLog, null, 2) }}</pre></details>
  </article>
</template>
