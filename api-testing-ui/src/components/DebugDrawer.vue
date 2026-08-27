<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref } from 'vue'
import { Bug, CheckCircle2, Play, X } from 'lucide-vue-next'

import type { DebugResult, DebugTraceStep } from '../api/contracts'
import { failureCategoryLabel, statusLabel } from '../utils/executionPresentation'
import DebugTrace from './DebugTrace.vue'

const props = withDefaults(defineProps<{ caseVersionId: string; environmentRevisionId: string; environmentLabel?: string; result?: DebugResult | null; running?: boolean; canResume?: boolean; open?: boolean; error?: string; baselineAdopting?: boolean; baselineMessage?: string; baselineError?: string }>(), {
  environmentLabel: '', result: null, running: false, canResume: false, open: true, error: '',
  baselineAdopting: false, baselineMessage: '', baselineError: '',
})
const emit = defineEmits<{
  submit: [input: { caseVersionIds: string[]; environmentRevisionId: string }]
  resume: []
  adopt: [input: { caseVersionId: string; executionCaseId: string }]
  close: []
  'edit-step': [target: { stage: 'setup' | 'main' | 'cleanup'; index: number }]
}>()

const displayTrace = computed<DebugTraceStep[]>(() => {
  if (!props.result) return []
  if (props.result.trace.length) return props.result.trace
  const method = typeof props.result.resolvedRequest.method === 'string' ? props.result.resolvedRequest.method : ''
  const path = typeof props.result.resolvedRequest.path === 'string' ? props.result.resolvedRequest.path : ''
  return [{
    stage: 'main', index: 0, name: [method, path].filter(Boolean).join(' ') || '当前接口',
    status: props.result.status, failureCategory: props.result.failureCategory,
    assertions: props.result.assertions, extractedVariableNames: [], missingVariableNames: [],
    request: props.result.resolvedRequest, response: props.result.sanitizedResponse,
    error: props.result.errorMessage, attempt: 1, maxAttempts: 1,
  }]
})

const responseStatus = computed(() => {
  const value = props.result?.sanitizedResponse?.status_code
  return typeof value === 'number' || typeof value === 'string' ? String(value) : '未获取'
})
const responseBody = computed<Record<string, unknown> | null>(() => {
  const body = props.result?.sanitizedResponse?.body
  if (body && typeof body === 'object' && !Array.isArray(body)) return body as Record<string, unknown>
  if (typeof body !== 'string') return null
  try {
    const parsed = JSON.parse(body)
    return parsed && typeof parsed === 'object' && !Array.isArray(parsed) ? parsed as Record<string, unknown> : null
  } catch {
    return null
  }
})
const businessCode = computed(() => {
  const value = responseBody.value?.code
  return value === undefined || value === null ? '未提供' : String(value)
})
const assertionRows = computed(() => (props.result?.assertions || []).filter(
  (item): item is Record<string, unknown> => Boolean(item && typeof item === 'object'),
))
const failedAssertion = computed(() => assertionRows.value.find(item => item.passed === false))
const assertionConclusion = computed(() => failedAssertion.value
  ? String(failedAssertion.value.message || '存在未满足的断言')
  : assertionRows.value.length ? `${assertionRows.value.length} 条断言均满足` : '未配置断言')
const repairSuggestion = computed(() => {
  if (props.result?.failureCategory === 'business_response') return '请将业务码断言改为精确预期值；成功场景通常为 0 或 200，失败场景应填写明确错误码。'
  if (failedAssertion.value) return '请核对实际响应与预期值，修改请求数据或断言后重新调试。'
  if (props.result?.status === 'BROKEN') return '请先检查执行环境、网络和变量配置，再重新调试。'
  return ''
})

function submit(): void {
  emit('submit', { caseVersionIds: [props.caseVersionId], environmentRevisionId: props.environmentRevisionId })
}

const drawer = ref<HTMLElement | null>(null)
let returnTarget: HTMLElement | null = null
onMounted(async () => {
  returnTarget = document.activeElement instanceof HTMLElement ? document.activeElement : null
  await nextTick()
  const first = focusableElements()[0]
  if (first) first.focus()
  else drawer.value?.focus()
})
onBeforeUnmount(() => returnTarget?.focus())

function focusableElements(): HTMLElement[] {
  if (!drawer.value) return []
  return Array.from(drawer.value.querySelectorAll<HTMLElement>(
    'button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), a[href], [tabindex]:not([tabindex="-1"])',
  )).filter(element => !element.hasAttribute('hidden'))
}

function handleKeydown(event: KeyboardEvent): void {
  if (event.key === 'Escape') {
    emit('close')
    return
  }
  if (event.key !== 'Tab' || !drawer.value) return
  const focusable = focusableElements()
  if (!focusable.length) {
    event.preventDefault()
    drawer.value.focus()
    return
  }
  const first = focusable[0]
  const last = focusable[focusable.length - 1]
  if (event.shiftKey && document.activeElement === first) {
    event.preventDefault()
    last.focus()
  } else if (!event.shiftKey && document.activeElement === last) {
    event.preventDefault()
    first.focus()
  }
}
</script>

<template>
  <aside v-if="open" ref="drawer" class="debug-drawer" role="dialog" aria-modal="true" aria-labelledby="debug-drawer-title" tabindex="-1" @keydown="handleKeydown">
    <header><div><Bug :size="18" /><h2 id="debug-drawer-title">在线调试</h2></div><button class="mini-icon" type="button" title="关闭调试" @click="emit('close')"><X :size="17" /></button></header>
    <div class="debug-context"><span>草稿版本</span><code>{{ caseVersionId || '请先保存' }}</code><span>执行环境</span><strong>{{ environmentLabel || '未选择环境' }}</strong></div>
    <button v-if="canResume" data-testid="debug-resume" class="primary-command wide" type="button" :disabled="running" @click="emit('resume')"><Play :size="16" />{{ running ? '正在读取' : '继续查看进度' }}</button>
    <button v-else data-testid="debug-send" class="primary-command wide" type="button" :disabled="!caseVersionId || !environmentRevisionId || running" @click="submit"><Play :size="16" />{{ running ? '正在执行' : '发送调试请求' }}</button>
    <p v-if="running" class="state-message" aria-live="polite">请求已进入执行队列，正在等待真实结果...</p>
    <p v-if="error" class="state-message state-error" role="alert">{{ error }}</p>
    <section v-if="result" class="debug-result">
      <div class="result-status"><strong :class="result.status === 'PASSED' ? 'status-pass' : 'status-fail'">{{ statusLabel(result.status) }}</strong><span v-if="result.failureCategory">{{ failureCategoryLabel(result.failureCategory) }}</span><span>{{ result.durationMs }} ms</span></div>
      <section data-testid="debug-conclusion" class="debug-conclusion" :class="result.status === 'PASSED' ? 'conclusion-pass' : 'conclusion-fail'">
        <header><CheckCircle2 v-if="result.status === 'PASSED'" :size="18" /><Bug v-else :size="18" /><strong>{{ result.status === 'PASSED' ? '调试通过' : '调试未通过' }}</strong></header>
        <dl>
          <div><dt>HTTP 状态</dt><dd>{{ responseStatus }}</dd></div>
          <div><dt>业务码</dt><dd>{{ businessCode }}</dd></div>
          <div><dt>断言结论</dt><dd>{{ assertionConclusion }}</dd></div>
        </dl>
        <p v-if="repairSuggestion">{{ repairSuggestion }}</p>
      </section>
      <p v-if="result.errorMessage" class="state-message state-error">{{ result.errorMessage }}</p>
      <DebugTrace :trace="displayTrace" @edit-step="emit('edit-step', $event)" />
      <details class="debug-raw-evidence"><summary>原始执行证据</summary>
        <details><summary>已解析请求</summary><pre>{{ JSON.stringify(result.resolvedRequest, null, 2) }}</pre></details>
        <details><summary>脱敏响应</summary><pre>{{ JSON.stringify(result.sanitizedResponse, null, 2) }}</pre></details>
        <details><summary>断言结果</summary><pre>{{ JSON.stringify(result.assertions, null, 2) }}</pre></details>
        <details><summary>执行日志</summary><pre>{{ result.logs.join('\n') }}</pre></details>
      </details>
      <button v-if="result.status === 'PASSED'" data-testid="adopt-baseline" class="baseline-command" type="button" :disabled="baselineAdopting || Boolean(baselineMessage)" @click="emit('adopt', { caseVersionId, executionCaseId: result.executionCaseId })"><CheckCircle2 :size="16" />{{ baselineAdopting ? '采纳中…' : baselineMessage || '采纳为基线' }}</button>
      <p v-if="baselineMessage" data-testid="baseline-success" class="state-message status-pass" role="status">{{ baselineMessage }}，后续可直接加入回归执行。</p>
      <p v-if="baselineError" data-testid="baseline-error" class="state-message state-error" role="alert">{{ baselineError }}</p>
    </section>
  </aside>
</template>
