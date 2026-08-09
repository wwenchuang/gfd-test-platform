<script setup lang="ts">
import { Bug, CheckCircle2, Play, X } from 'lucide-vue-next'

import type { DebugResult } from '../api/contracts'

const props = withDefaults(defineProps<{ caseVersionId: string; environmentRevisionId: string; result?: DebugResult | null; running?: boolean; open?: boolean }>(), {
  result: null, running: false, open: true,
})
const emit = defineEmits<{
  submit: [input: { caseVersionIds: string[]; environmentRevisionId: string }]
  adopt: [input: { caseVersionId: string; executionCaseId: string }]
  close: []
}>()

function submit(): void {
  emit('submit', { caseVersionIds: [props.caseVersionId], environmentRevisionId: props.environmentRevisionId })
}
</script>

<template>
  <aside v-if="open" class="debug-drawer" aria-label="在线调试">
    <header><div><Bug :size="18" /><h2>在线调试</h2></div><button class="mini-icon" type="button" title="关闭调试" @click="emit('close')"><X :size="17" /></button></header>
    <div class="debug-context"><span>草稿版本</span><code>{{ caseVersionId || '请先保存' }}</code><span>执行环境</span><code>{{ environmentRevisionId || '未选择' }}</code></div>
    <button data-testid="debug-send" class="primary-command wide" type="button" :disabled="!caseVersionId || !environmentRevisionId || running" @click="submit"><Play :size="16" />{{ running ? '正在执行' : '发送调试请求' }}</button>
    <p v-if="running" class="state-message" aria-live="polite">请求已进入执行队列，正在等待真实结果...</p>
    <section v-if="result" class="debug-result">
      <div class="result-status"><strong :class="result.status === 'PASSED' ? 'status-pass' : 'status-fail'">{{ result.status }}</strong><span v-if="result.failureCategory">{{ result.failureCategory }}</span></div>
      <details open><summary>已解析请求</summary><pre>{{ JSON.stringify(result.resolvedRequest, null, 2) }}</pre></details>
      <details open><summary>脱敏响应</summary><pre>{{ JSON.stringify(result.sanitizedResponse, null, 2) }}</pre></details>
      <details><summary>断言结果</summary><pre>{{ JSON.stringify(result.assertions, null, 2) }}</pre></details>
      <details><summary>执行日志</summary><pre>{{ result.logs.join('\n') }}</pre></details>
      <button v-if="result.status === 'PASSED'" data-testid="adopt-baseline" class="baseline-command" type="button" @click="emit('adopt', { caseVersionId, executionCaseId: result.executionCaseId })"><CheckCircle2 :size="16" />采纳为基线</button>
    </section>
  </aside>
</template>
