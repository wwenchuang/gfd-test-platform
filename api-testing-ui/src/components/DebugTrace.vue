<script setup lang="ts">
import { CheckCircle2, CircleDashed, Pencil, XCircle } from 'lucide-vue-next'

import { statusLabel } from '../utils/executionPresentation'
import type { DebugTraceStep } from '../api/contracts'

withDefaults(defineProps<{ trace?: DebugTraceStep[] }>(), { trace: () => [] })
const emit = defineEmits<{ 'edit-step': [target: { stage: 'setup' | 'main' | 'cleanup'; index: number }] }>()

function stageLabel(stage: DebugTraceStep['stage']): string {
  return { setup: '前置步骤', main: '主体请求', cleanup: '清理步骤' }[stage]
}

function passed(status: string): boolean {
  return status.toUpperCase() === 'PASSED'
}

function skipped(status: string): boolean {
  return status.toUpperCase() === 'SKIPPED'
}
</script>

<template>
  <section data-testid="debug-trace" class="debug-trace">
    <header><strong>执行步骤</strong><span>{{ trace.length }} 步</span></header>
    <p v-if="!trace.length" class="compact-empty">本次执行没有分步骤追踪信息，可在下方查看原始证据。</p>
    <article v-for="step in trace" :key="`${step.stage}-${step.index}-${step.attempt}`" :class="[`trace-${step.status.toLowerCase()}`, { 'trace-cleanup-failed': step.stage === 'cleanup' && !passed(step.status) }]">
      <div class="trace-marker">
        <CheckCircle2 v-if="passed(step.status)" :size="17" />
        <CircleDashed v-else-if="skipped(step.status)" :size="17" />
        <XCircle v-else :size="17" />
      </div>
      <div class="trace-content">
        <header><span>{{ stageLabel(step.stage) }}{{ step.stage === 'main' ? '' : ` ${step.index + 1}` }}</span><strong>{{ step.name }}</strong><b>{{ step.status.toUpperCase() === 'FAILED' ? '未通过' : statusLabel(step.status) }}</b></header>
        <div class="trace-metadata">
          <span v-if="step.attempt > 1 || step.maxAttempts > 1">尝试 {{ step.attempt }}/{{ step.maxAttempts }}</span>
          <span v-if="step.assertions.length">断言 {{ step.assertions.length }}</span>
          <span v-if="step.extractedVariableNames.length">输出 {{ step.extractedVariableNames.join('、') }}</span>
          <span v-if="step.missingVariableNames.length" class="trace-missing">缺少 {{ step.missingVariableNames.join('、') }}</span>
        </div>
        <p v-if="step.error">{{ step.error }}</p>
        <details><summary>查看该步骤证据</summary><div class="trace-evidence"><label>请求<pre>{{ JSON.stringify(step.request, null, 2) }}</pre></label><label>响应<pre>{{ JSON.stringify(step.response, null, 2) }}</pre></label><label>断言<pre>{{ JSON.stringify(step.assertions, null, 2) }}</pre></label></div></details>
      </div>
      <button :data-testid="`edit-debug-step-${step.stage}-${step.index}`" class="mini-icon" type="button" :title="step.stage === 'main' ? '编辑主体请求' : '编辑此步骤'" @click="emit('edit-step', { stage: step.stage, index: step.index })"><Pencil :size="14" /></button>
    </article>
  </section>
</template>
