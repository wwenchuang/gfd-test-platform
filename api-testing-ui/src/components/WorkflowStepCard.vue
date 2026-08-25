<script setup lang="ts">
import { computed } from 'vue'
import { ArrowDown, ArrowUp, ChevronDown, ChevronRight, Copy, Trash2 } from 'lucide-vue-next'

import type { InlineWorkflowStep } from '../api/contracts'

const props = defineProps<{
  step: InlineWorkflowStep
  index: number
  stage: 'setup' | 'cleanup'
  active: boolean
  issueCount: number
  first: boolean
  last: boolean
}>()

const emit = defineEmits<{
  toggle: []
  enabled: [enabled: boolean]
  move: [offset: number]
  duplicate: []
  remove: []
}>()

const parameterCount = computed(() => {
  const request = props.step.request
  return ['path_params', 'query', 'headers', 'cookies']
    .reduce((total, field) => total + Object.keys(request[field as keyof typeof request] as Record<string, unknown> || {}).length, 0)
    + (request.body === null || request.body === undefined ? 0 : 1)
})
const assertionCount = computed(() => props.step.assertions.filter(item => item.enabled !== false).length)
const extractionCount = computed(() => props.step.extractions.length)
</script>

<template>
  <article class="workflow-step-card" :class="{ active }">
    <div :data-testid="`${stage}-step-summary-${index}`" class="workflow-step-summary">
      <button
        :data-testid="`${stage}-step-toggle-${index}`"
        class="workflow-step-toggle"
        type="button"
        :aria-expanded="active ? 'true' : 'false'"
        @click="emit('toggle')"
      >
        <ChevronDown v-if="active" :size="15" />
        <ChevronRight v-else :size="15" />
        <span class="workflow-order">{{ index + 1 }}</span>
        <span class="workflow-step-copy">
          <b>{{ step.name }}</b>
          <code>{{ step.request.method }} {{ step.request.path }}</code>
        </span>
        <span class="workflow-step-metrics">
          <i>参数 {{ parameterCount }}</i>
          <i>断言 {{ assertionCount }}</i>
          <i>提取 {{ extractionCount }}</i>
          <i v-if="step.polling">轮询</i>
          <i v-if="issueCount" class="workflow-step-issues">错误 {{ issueCount }}</i>
        </span>
      </button>
      <span class="workflow-step-actions">
        <label class="workflow-step-enable" :title="step.enabled ? '停用步骤' : '启用步骤'">
          <input :checked="step.enabled" type="checkbox" :aria-label="step.enabled ? '停用步骤' : '启用步骤'" @change="emit('enabled', ($event.target as HTMLInputElement).checked)" />
          <span>{{ step.enabled ? '启用' : '停用' }}</span>
        </label>
        <button :data-testid="`${stage}-step-up-${index}`" class="mini-icon" type="button" title="上移" :disabled="first" @click="emit('move', -1)"><ArrowUp :size="14" /></button>
        <button :data-testid="`${stage}-step-down-${index}`" class="mini-icon" type="button" title="下移" :disabled="last" @click="emit('move', 1)"><ArrowDown :size="14" /></button>
        <button :data-testid="`${stage}-step-duplicate-${index}`" class="mini-icon" type="button" title="复制步骤" @click="emit('duplicate')"><Copy :size="14" /></button>
        <button :data-testid="`${stage}-step-remove-${index}`" class="mini-icon danger" type="button" title="删除步骤" @click="emit('remove')"><Trash2 :size="14" /></button>
      </span>
    </div>
    <div v-if="active" :data-testid="`${stage}-step-body-${index}`" class="workflow-step-body"><slot /></div>
  </article>
</template>
