<script setup lang="ts">
import { nextTick, onBeforeUnmount, onMounted, ref } from 'vue'
import { Bug, CheckCircle2, Play, X } from 'lucide-vue-next'

import type { DebugResult } from '../api/contracts'
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
      <div class="result-status"><strong :class="result.status === 'PASSED' ? 'status-pass' : 'status-fail'">{{ result.status }}</strong><span v-if="result.failureCategory">{{ result.failureCategory }}</span><span>{{ result.durationMs }} ms</span></div>
      <p v-if="result.errorMessage" class="state-message state-error">{{ result.errorMessage }}</p>
      <DebugTrace :trace="result.trace" @edit-step="emit('edit-step', $event)" />
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
