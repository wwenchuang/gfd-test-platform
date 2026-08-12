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
const requestUrl = computed(() => String(request.value.url || request.value.resolved_url || ''))
const responseStatus = computed(() => Number(response.value.status_code || response.value.status || 0))
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
    <details open><summary>请求明细</summary><pre>{{ JSON.stringify(request, null, 2) }}</pre></details>
    <details open><summary>响应明细</summary><pre>{{ JSON.stringify(response, null, 2) }}</pre></details>
    <details open class="assertion-evidence"><summary>断言结果（{{ assertions.length }}）</summary><div v-if="assertions.length" class="assertion-result-list"><div v-for="(item, index) in assertions" :key="index" :class="item.passed === false ? 'assertion-failed' : 'assertion-passed'"><CircleX v-if="item.passed === false" :size="15" /><CheckCircle2 v-else :size="15" /><span><strong>{{ item.message || item.type || `断言 ${index + 1}` }}</strong><small>期望 {{ item.expected ?? '-' }} · 实际 {{ item.actual ?? '-' }}</small></span></div></div><p v-else class="state-message">本用例没有配置断言</p></details>
    <details><summary>执行轨迹（{{ trace.length }}）</summary><pre>{{ JSON.stringify(trace, null, 2) }}</pre></details>
  </section>
</template>
