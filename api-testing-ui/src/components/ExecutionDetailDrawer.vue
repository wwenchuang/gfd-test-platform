<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { X } from 'lucide-vue-next'

import type { ExecutionCaseResult, ExecutionView } from '../api/contracts'
import CaseResultList from './CaseResultList.vue'
import ReportSummary from './ReportSummary.vue'
import CaseEvidence from './CaseEvidence.vue'

const props = defineProps<{ execution: ExecutionView; initialCaseId?: string }>()
const emit = defineEmits<{ close: []; edit: [result: ExecutionCaseResult, execution: ExecutionView]; rerun: [execution: ExecutionView] }>()
const active = ref<ExecutionCaseResult | null>(props.execution.case_results.find(item => item.execution_case_id === props.initialCaseId) || props.execution.case_results[0] || null)
const duration = computed(() => props.execution.case_results.reduce((total, item) => total + item.duration_ms, 0))
const drawer = ref<HTMLElement | null>(null)
let returnTarget: HTMLElement | null = null

onMounted(async () => {
  returnTarget = document.activeElement instanceof HTMLElement ? document.activeElement : null
  await nextTick()
  focusableElements()[0]?.focus()
})
onBeforeUnmount(() => returnTarget?.focus())
watch(() => props.execution.case_results, results => {
  const selectedId = active.value?.execution_case_id
  active.value = results.find(item => item.execution_case_id === selectedId) || results[0] || null
})
watch(() => props.initialCaseId, id => {
  if (id) active.value = props.execution.case_results.find(item => item.execution_case_id === id) || active.value
})

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
  if (event.key !== 'Tab') return
  const focusable = focusableElements()
  if (!focusable.length) {
    event.preventDefault()
    drawer.value?.focus()
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
  <aside ref="drawer" class="execution-detail-drawer" role="dialog" aria-modal="true" aria-label="执行详情" tabindex="-1" @keydown="handleKeydown"><header><div><h2>执行详情</h2><span>{{ execution.execution_type === 'debug' ? '在线调试' : '自动回归' }}</span></div><button class="mini-icon" type="button" title="关闭详情" @click="emit('close')"><X :size="17" /></button></header><ReportSummary :summary="execution.summary" :duration-ms="duration" :environment-name="execution.environment_name" /><div class="execution-detail-grid"><CaseResultList :results="execution.case_results" :active-id="active?.execution_case_id" @select="active = $event" /><CaseEvidence v-if="active" :result="active" @edit="emit('edit', $event, execution)" @rerun="emit('rerun', execution)" /></div></aside>
</template>
