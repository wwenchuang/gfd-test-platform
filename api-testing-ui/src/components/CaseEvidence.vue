<script setup lang="ts">
import { computed } from 'vue'
import { CheckCircle2, CircleX, Pencil, RotateCw } from 'lucide-vue-next'

import type { ExecutionCaseResult } from '../api/contracts'
import { redactSensitiveEvidence, statusLabel } from '../utils/executionPresentation'
import FailureAnalysis from './FailureAnalysis.vue'

const props = defineProps<{ result: ExecutionCaseResult }>()
const emit = defineEmits<{ edit: [result: ExecutionCaseResult]; rerun: [result: ExecutionCaseResult] }>()
const detail = computed(() => props.result.sanitized_result || {})
const request = computed(() => redactSensitiveEvidence(detail.value.sanitized_request || detail.value.request || {}) as Record<string, unknown>)
const response = computed(() => redactSensitiveEvidence(detail.value.sanitized_response || detail.value.response || {}) as Record<string, unknown>)
const assertions = computed(() => {
  const value = detail.value.assertion_results || detail.value.assertions
  return Array.isArray(value) ? redactSensitiveEvidence(value) as Array<Record<string, unknown>> : []
})
const trace = computed(() => {
  const value = detail.value.trace || detail.value.logs
  return Array.isArray(value) ? redactSensitiveEvidence(value) as Array<Record<string, unknown>> : []
})
const workflowSteps = computed(() => trace.value.filter(item => item.phase === 'workflow_step'))
const requestUrl = computed(() => String(request.value.url || request.value.resolved_url || ''))
const responseStatus = computed(() => Number(response.value.status_code || response.value.status || 0))
function stageLabel(stage: unknown): string {
  return ({ setup: '前置步骤', main: '主体请求', cleanup: '清理步骤' } as Record<string, string>)[String(stage)] || '执行步骤'
}
</script>

<template>
  <section class="case-evidence">
    <header>
      <div><strong>{{ result.case_name || result.endpoint_summary || result.path }}</strong><code>{{ result.method }} {{ result.path }}</code></div>
      <div>
        <button data-testid="edit-case" class="secondary-command" type="button" @click="emit('edit', result)"><Pencil :size="14" />编辑用例</button>
        <button v-if="['FAILED','BROKEN'].includes(result.status)" data-testid="rerun-case" class="secondary-command" type="button" @click="emit('rerun', result)"><RotateCw :size="14" />重跑失败项</button>
      </div>
    </header>
    <FailureAnalysis :result="result" />
    <div class="evidence-summary">
      <div><span>状态</span><strong :class="`status-${result.status.toLowerCase()}`">{{ statusLabel(result.status) }}</strong></div>
      <div><span>请求</span><strong>{{ result.method }} {{ requestUrl || result.path }}</strong></div>
      <div><span>响应</span><strong>{{ responseStatus ? `HTTP ${responseStatus}` : '未收到响应' }}</strong></div>
      <div><span>耗时</span><strong>{{ result.duration_ms }} ms</strong></div>
    </div>
    <section v-if="workflowSteps.length" data-testid="workflow-evidence" class="workflow-evidence">
      <header><strong>分阶段执行证据</strong><span>{{ workflowSteps.length }} 个步骤</span></header>
      <article v-for="(step, index) in workflowSteps" :key="index" :class="`workflow-evidence-${String(step.status || '').toLowerCase()}`">
        <div class="workflow-evidence-head"><span>{{ stageLabel(step.stage) }}</span><strong>{{ step.name || `步骤 ${index + 1}` }}</strong><small v-if="Number(step.max_attempts || 1) > 1">第 {{ step.attempt }}/{{ step.max_attempts }} 次</small><b>{{ String(step.status).toUpperCase() === 'FAILED' ? '未通过' : statusLabel(String(step.status || '')) }}</b></div>
        <code>{{ (step.request as Record<string, unknown>)?.method || '' }} {{ (step.request as Record<string, unknown>)?.url || '' }}</code>
        <p v-if="step.error_message">{{ step.error_message }}</p>
        <details><summary>请求、响应与断言</summary><pre>{{ JSON.stringify({ request: step.request, response: step.response, assertions: step.assertions, extracted_variables: step.extracted_variables, missing_variables: step.missing_variables }, null, 2) }}</pre></details>
      </article>
    </section>
    <details open><summary>请求明细</summary><pre>{{ JSON.stringify(request, null, 2) }}</pre></details>
    <details open><summary>响应明细</summary><pre>{{ JSON.stringify(response, null, 2) }}</pre></details>
    <details open class="assertion-evidence"><summary>断言结果（{{ assertions.length }}）</summary><div v-if="assertions.length" class="assertion-result-list"><div v-for="(item, index) in assertions" :key="index" :class="item.passed === false ? 'assertion-failed' : 'assertion-passed'"><CircleX v-if="item.passed === false" :size="15" /><CheckCircle2 v-else :size="15" /><span><strong>{{ item.message || item.type || `断言 ${index + 1}` }}</strong><small>期望 {{ item.expected ?? '-' }} · 实际 {{ item.actual ?? '-' }}</small></span></div></div><p v-else class="state-message">本用例没有配置断言</p></details>
    <details><summary>执行轨迹（{{ trace.length }}）</summary><pre>{{ JSON.stringify(trace, null, 2) }}</pre></details>
  </section>
</template>
