<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { X } from 'lucide-vue-next'

import type { ExecutionCaseResult, ExecutionView } from '../api/contracts'
import { executionTypeLabel, hasLoadedCaseEvidence } from '../utils/executionPresentation'
import CaseResultList from './CaseResultList.vue'
import ReportSummary from './ReportSummary.vue'
import CaseEvidence from './CaseEvidence.vue'

const props = defineProps<{ execution: ExecutionView; initialCaseId?: string; loadingCaseKeys?: string[]; caseEvidenceErrors?: Record<string, string> }>()
const emit = defineEmits<{ close: []; edit: [result: ExecutionCaseResult, execution: ExecutionView]; rerun: [execution: ExecutionView]; loadEvidence: [result: ExecutionCaseResult] }>()
const active = ref<ExecutionCaseResult | null>(props.execution.case_results.find(item => item.execution_case_id === props.initialCaseId) || props.execution.case_results[0] || null)
const duration = computed(() => props.execution.case_results.reduce((total, item) => total + item.duration_ms, 0))
const drawer = ref<HTMLElement | null>(null)
const listPane = ref<HTMLElement | null>(null)
const evidencePane = ref<HTMLElement | null>(null)
let returnTarget: HTMLElement | null = null

onMounted(async () => {
  returnTarget = document.activeElement instanceof HTMLElement ? document.activeElement : null
  await nextTick()
  focusableElements()[0]?.focus()
  await revealActiveRow()
})
onBeforeUnmount(() => returnTarget?.focus())
watch(() => props.execution.case_results, results => {
  const selectedId = active.value?.execution_case_id
  active.value = results.find(item => item.execution_case_id === selectedId) || results[0] || null
})
watch(() => props.initialCaseId, id => {
  if (id) active.value = props.execution.case_results.find(item => item.execution_case_id === id) || active.value
})
watch(() => {
  const result = active.value
  return [
    result?.execution_case_id,
    result?.status,
    result?.evidence_loaded,
    Object.keys(result?.sanitized_result || {}).length,
  ]
}, async () => {
  if (active.value && !hasLoadedCaseEvidence(active.value)) emit('loadEvidence', active.value)
  await nextTick()
  if (evidencePane.value) evidencePane.value.scrollTop = 0
  await revealActiveRow()
}, { immediate: true })

async function revealActiveRow(): Promise<void> {
  await nextTick()
  const pane = listPane.value
  const selected = pane?.querySelector<HTMLElement>('.active')
  if (!pane || !selected) return
  const bounds = pane.getBoundingClientRect()
  const row = selected.getBoundingClientRect()
  if (row.height > pane.clientHeight || row.top < bounds.top) pane.scrollTop += row.top - bounds.top
  else if (row.bottom > bounds.bottom) pane.scrollTop += row.bottom - bounds.bottom
}

function evidenceKey(result: ExecutionCaseResult): string {
  return `${props.execution.id}:${result.execution_case_id}`
}

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
  <aside ref="drawer" class="execution-detail-drawer" role="dialog" aria-modal="true" aria-label="执行详情" tabindex="-1" @keydown="handleKeydown">
    <header><div><h2>执行详情</h2><span>{{ executionTypeLabel(execution) }}</span></div><button class="mini-icon" type="button" title="关闭详情" @click="emit('close')"><X :size="17" /></button></header>
    <ReportSummary :summary="execution.summary" :duration-ms="duration" :environment-name="execution.environment_name" />
    <div class="execution-detail-grid">
      <div ref="listPane" class="execution-detail-list" role="region" aria-label="用例结果列表" tabindex="0">
        <CaseResultList :results="execution.case_results" :active-id="active?.execution_case_id" @select="active = $event" />
      </div>
      <div ref="evidencePane" class="execution-detail-evidence" role="region" aria-label="当前用例证据" tabindex="0">
        <CaseEvidence v-if="active" :result="active" :loading="loadingCaseKeys?.includes(evidenceKey(active))" :error="caseEvidenceErrors?.[evidenceKey(active)]" @retry="emit('loadEvidence', $event)" @edit="emit('edit', $event, execution)" @rerun="emit('rerun', execution)" />
      </div>
    </div>
  </aside>
</template>
