<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { CheckCircle2, CircleX, Pencil, RotateCw } from 'lucide-vue-next'

import type { ExecutionCaseResult } from '../api/contracts'
import { formatCompactEvidenceValue, hasLoadedCaseEvidence, redactSensitiveEvidence, statusLabel } from '../utils/executionPresentation'
import FailureAnalysis from './FailureAnalysis.vue'

const props = defineProps<{ result: ExecutionCaseResult; loading?: boolean; error?: string }>()
const emit = defineEmits<{ edit: [result: ExecutionCaseResult]; rerun: [result: ExecutionCaseResult]; retry: [result: ExecutionCaseResult] }>()
const JSON_PREVIEW_LIMIT = 12_000
const ASSERTION_PREVIEW_LIMIT = 160
const expandedBlocks = ref<Set<string>>(new Set())
const evidenceReady = computed(() => hasLoadedCaseEvidence(props.result))
const detail = computed(() => props.result.sanitized_result || {})
const request = computed(() => redactSensitiveEvidence(detail.value.sanitized_request || detail.value.request || {}) as Record<string, unknown>)
const response = computed(() => redactSensitiveEvidence(detail.value.sanitized_response || detail.value.response || {}) as Record<string, unknown>)
const assertions = computed(() => {
  const value = detail.value.assertion_results || detail.value.assertions
  return Array.isArray(value) ? redactSensitiveEvidence(value) as Array<Record<string, unknown>> : []
})
const assertionRows = computed(() => assertions.value.map((item, index) => ({
  item,
  index,
  expectedBlock: assertionBlock(item.expected, `assertion-expected-${index}`),
  actualBlock: assertionBlock(item.actual, `assertion-actual-${index}`),
})))
const trace = computed(() => {
  const value = detail.value.trace || detail.value.logs
  return Array.isArray(value) ? redactSensitiveEvidence(value) as Array<Record<string, unknown>> : []
})
const workflowSteps = computed(() => trace.value.filter(item => item.phase === 'workflow_step'))
const requestUrl = computed(() => String(request.value.url || request.value.resolved_url || ''))
const responseStatus = computed(() => Number(response.value.status_code || response.value.status || 0))
const requestBlock = computed(() => jsonBlock(request.value, 'request'))
const responseBlock = computed(() => jsonBlock(response.value, 'response'))
const traceBlock = computed(() => jsonBlock(trace.value, 'trace'))
const workflowBlocks = computed(() => workflowSteps.value.map((step, index) => ({
  step,
  key: `workflow-${index}`,
  block: jsonBlock({
    request: step.request,
    response: step.response,
    assertions: step.assertions,
    extracted_variables: step.extracted_variables,
    missing_variables: step.missing_variables,
  }, `workflow-${index}`),
})))

watch(() => props.result.execution_case_id, () => {
  expandedBlocks.value = new Set()
})

function stageLabel(stage: unknown): string {
  return ({ setup: '前置步骤', main: '主体请求', cleanup: '清理步骤' } as Record<string, string>)[String(stage)] || '执行步骤'
}

function assertionBlock(value: unknown, key: string): { text: string; oversized: boolean; expanded: boolean; length: number } {
  const full = formatCompactEvidenceValue(value, Number.MAX_SAFE_INTEGER)
  const oversized = full.length > ASSERTION_PREVIEW_LIMIT
  const expanded = oversized && expandedBlocks.value.has(key)
  return {
    text: expanded ? full : formatCompactEvidenceValue(value, ASSERTION_PREVIEW_LIMIT),
    oversized,
    expanded,
    length: full.length,
  }
}

function jsonBlock(value: unknown, key: string): { text: string; oversized: boolean; expanded: boolean; length: number } {
  const full = JSON.stringify(value, null, 2) || ''
  const oversized = full.length > JSON_PREVIEW_LIMIT
  const expanded = oversized && expandedBlocks.value.has(key)
  return {
    text: oversized && !expanded
      ? `${full.slice(0, JSON_PREVIEW_LIMIT)}\n\n…内容较大，已先展示前 ${JSON_PREVIEW_LIMIT.toLocaleString('zh-CN')} 个字符，剩余 ${(full.length - JSON_PREVIEW_LIMIT).toLocaleString('zh-CN')} 个字符。`
      : full,
    oversized,
    expanded,
    length: full.length,
  }
}

function toggleBlock(key: string): void {
  const next = new Set(expandedBlocks.value)
  if (next.has(key)) next.delete(key)
  else next.add(key)
  expandedBlocks.value = next
}
</script>

<template>
  <section class="case-evidence">
    <header>
      <div><strong>{{ result.case_name || result.endpoint_summary || result.path }}</strong><code>{{ result.method }} {{ result.path }}</code></div>
      <div>
        <button data-testid="edit-case" class="secondary-command" type="button" @click="emit('edit', result)"><Pencil :size="14" />编辑用例</button>
        <button v-if="['FAILED','BROKEN'].includes(result.status)" data-testid="rerun-case" class="secondary-command" type="button" @click="emit('rerun', result)"><RotateCw :size="14" />仅重跑当前失败项</button>
      </div>
    </header>
    <div v-if="error" class="evidence-load-state inline-error" role="alert">
      <span>{{ error }}</span>
      <button data-testid="retry-case-evidence" class="secondary-command" type="button" @click="emit('retry', result)">重新读取证据</button>
    </div>
    <p v-else-if="loading" class="evidence-load-state state-message">正在读取当前用例的请求、响应和断言，请稍候…</p>
    <p v-else-if="!evidenceReady" class="evidence-load-state state-message">选择用例后读取请求、响应、断言和执行轨迹。</p>
    <template v-else>
    <FailureAnalysis :result="result" />
    <div class="evidence-summary">
      <div><span>状态</span><strong :class="`status-${result.status.toLowerCase()}`">{{ statusLabel(result.status) }}</strong></div>
      <div><span>请求</span><strong>{{ result.method }} {{ requestUrl || result.path }}</strong></div>
      <div><span>响应</span><strong>{{ responseStatus ? `HTTP ${responseStatus}` : '未收到响应' }}</strong></div>
      <div><span>耗时</span><strong>{{ result.duration_ms }} ms</strong></div>
    </div>
    <section v-if="workflowSteps.length" data-testid="workflow-evidence" class="workflow-evidence">
      <header><strong>分阶段执行证据</strong><span>{{ workflowSteps.length }} 个步骤</span></header>
      <article v-for="({ step, key, block }, index) in workflowBlocks" :key="index" :class="`workflow-evidence-${String(step.status || '').toLowerCase()}`">
        <div class="workflow-evidence-head"><span>{{ stageLabel(step.stage) }}</span><strong>{{ step.name || `步骤 ${index + 1}` }}</strong><small v-if="Number(step.max_attempts || 1) > 1">第 {{ step.attempt }}/{{ step.max_attempts }} 次</small><b>{{ String(step.status).toUpperCase() === 'FAILED' ? '未通过' : statusLabel(String(step.status || '')) }}</b></div>
        <code>{{ (step.request as Record<string, unknown>)?.method || '' }} {{ (step.request as Record<string, unknown>)?.url || '' }}</code>
        <p v-if="step.error_message">{{ step.error_message }}</p>
        <details><summary>请求、响应与断言</summary><pre>{{ block.text }}</pre><div v-if="block.oversized" class="evidence-preview-actions"><span>步骤证据共 {{ block.length.toLocaleString('zh-CN') }} 个字符</span><button class="text-command" type="button" @click="toggleBlock(key)">{{ block.expanded ? '恢复精简预览' : '显示完整步骤证据' }}</button></div></details>
      </article>
    </section>
    <details open><summary>请求明细</summary><pre>{{ requestBlock.text }}</pre><div v-if="requestBlock.oversized" class="evidence-preview-actions"><span>请求共 {{ requestBlock.length.toLocaleString('zh-CN') }} 个字符</span><button data-testid="expand-request-evidence" class="text-command" type="button" @click="toggleBlock('request')">{{ requestBlock.expanded ? '恢复精简预览' : '显示完整请求' }}</button></div></details>
    <details data-testid="response-evidence" :open="!responseBlock.oversized"><summary>响应明细<span v-if="responseBlock.oversized"> · {{ responseBlock.length.toLocaleString('zh-CN') }} 字符，默认收起</span></summary><pre>{{ responseBlock.text }}</pre><div v-if="responseBlock.oversized" class="evidence-preview-actions"><span>响应共 {{ responseBlock.length.toLocaleString('zh-CN') }} 个字符</span><button data-testid="expand-response-evidence" class="text-command" type="button" @click="toggleBlock('response')">{{ responseBlock.expanded ? '恢复精简预览' : '显示完整响应' }}</button></div></details>
    <details open class="assertion-evidence">
      <summary>断言结果（{{ assertions.length }}）</summary>
      <div v-if="assertions.length" class="assertion-result-list">
        <div v-for="row in assertionRows" :key="row.index" :class="row.item.passed === false ? 'assertion-failed' : 'assertion-passed'">
          <CircleX v-if="row.item.passed === false" :size="15" />
          <CheckCircle2 v-else :size="15" />
          <span>
            <strong>{{ row.item.message || row.item.type || `断言 ${row.index + 1}` }}</strong>
            <small class="assertion-value-list">
              <span :data-testid="`assertion-expected-${row.index}`">
                <b>期望</b>
                <span>{{ row.expectedBlock.text }}</span>
                <button
                  v-if="row.expectedBlock.oversized"
                  :data-testid="`expand-assertion-expected-${row.index}`"
                  class="text-command"
                  type="button"
                  @click="toggleBlock(`assertion-expected-${row.index}`)"
                >{{ row.expectedBlock.expanded ? '收起完整值' : '显示完整值' }}</button>
              </span>
              <span :data-testid="`assertion-actual-${row.index}`">
                <b>实际</b>
                <span>{{ row.actualBlock.text }}</span>
                <button
                  v-if="row.actualBlock.oversized"
                  :data-testid="`expand-assertion-actual-${row.index}`"
                  class="text-command"
                  type="button"
                  @click="toggleBlock(`assertion-actual-${row.index}`)"
                >{{ row.actualBlock.expanded ? '收起完整值' : '显示完整值' }}</button>
              </span>
            </small>
          </span>
        </div>
      </div>
      <p v-else class="state-message">本用例没有配置断言</p>
    </details>
    <details><summary>执行轨迹（{{ trace.length }}）</summary><pre>{{ traceBlock.text }}</pre><div v-if="traceBlock.oversized" class="evidence-preview-actions"><span>轨迹共 {{ traceBlock.length.toLocaleString('zh-CN') }} 个字符</span><button data-testid="expand-trace-evidence" class="text-command" type="button" @click="toggleBlock('trace')">{{ traceBlock.expanded ? '恢复精简预览' : '显示完整轨迹' }}</button></div></details>
    </template>
  </section>
</template>
