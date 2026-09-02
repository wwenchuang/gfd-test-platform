<script setup lang="ts">
import { computed, nextTick, onUpdated, ref, watch } from 'vue'

import type { ExecutionCaseResult } from '../api/contracts'
import { statusLabel } from '../utils/executionPresentation'

const props = withDefaults(defineProps<{ results: ExecutionCaseResult[]; activeId?: string; rowTestId?: string }>(), {
  rowTestId: 'case-result-row',
})
const emit = defineEmits<{ select: [result: ExecutionCaseResult] }>()
const query = ref('')
const page = ref(1)
const root = ref<HTMLElement | null>(null)
const PAGE_SIZE = 50
const searchable = computed(() => props.results.length > PAGE_SIZE)
const filteredResults = computed(() => {
  const keyword = query.value.trim().toLocaleLowerCase()
  if (!keyword) return props.results
  return props.results.filter(result => [
    result.case_name, result.endpoint_summary, result.method, result.path, statusLabel(result.status),
  ].join(' ').toLocaleLowerCase().includes(keyword))
})
const pageCount = computed(() => Math.max(1, Math.ceil(filteredResults.value.length / PAGE_SIZE)))
const pagedResults = computed(() => {
  const start = (page.value - 1) * PAGE_SIZE
  return filteredResults.value.slice(start, start + PAGE_SIZE)
})
const rangeLabel = computed(() => {
  if (!filteredResults.value.length) return '没有匹配用例'
  const start = (page.value - 1) * PAGE_SIZE + 1
  const end = Math.min(page.value * PAGE_SIZE, filteredResults.value.length)
  return `第 ${start}-${end} 条，共 ${filteredResults.value.length} 条`
})

function alignActive(): void {
  const selected = root.value?.querySelector<HTMLElement>('.active')
  const pane = root.value?.parentElement
  if (!selected || !pane) return
  const bounds = pane.getBoundingClientRect()
  const row = selected.getBoundingClientRect()
  if (row.top < bounds.top) pane.scrollTop += row.top - bounds.top
  else if (row.bottom > bounds.bottom) pane.scrollTop += row.bottom - bounds.bottom
}

async function revealActive(activeId = props.activeId): Promise<void> {
  if (!activeId) return
  const index = filteredResults.value.findIndex(result => result.execution_case_id === activeId)
  if (index < 0) return
  page.value = Math.floor(index / PAGE_SIZE) + 1
  await nextTick()
  if (typeof window.requestAnimationFrame === 'function') {
    await new Promise<void>(resolve => window.requestAnimationFrame(() => resolve()))
  }
  alignActive()
}

watch(query, async () => {
  page.value = 1
  await revealActive()
})
watch([() => props.activeId, () => props.results], ([activeId]) => revealActive(activeId), { immediate: true })
watch(pageCount, count => {
  if (page.value > count) page.value = count
})
onUpdated(alignActive)
</script>

<template>
  <div ref="root" class="case-result-list">
    <div v-if="searchable" class="case-result-list-toolbar">
      <input v-model="query" data-testid="case-result-search" aria-label="搜索用例结果" placeholder="搜索用例名称或接口路径" />
      <span>{{ rangeLabel }}</span>
    </div>
    <div class="case-result-rows">
      <button v-for="result in pagedResults" :key="result.execution_case_id" :data-testid="rowTestId" type="button" :class="{ active: result.execution_case_id === activeId }" @click="emit('select', result)"><span :class="['status-dot', `dot-${result.status.toLowerCase()}`]" /><span><strong>{{ result.case_name || result.endpoint_summary || result.path }} <em v-if="result.execution_role === 'dependency'" data-testid="dependency-role" class="case-role">前置</em></strong><small>{{ result.method }} {{ result.path }}</small></span><b :class="`status-${result.status.toLowerCase()}`">{{ statusLabel(result.status) }}</b><time>{{ result.duration_ms }} ms</time></button>
    </div>
    <nav v-if="searchable && pageCount > 1" class="case-result-pagination" aria-label="用例结果分页">
      <button type="button" :disabled="page === 1" @click="page -= 1">上一页</button>
      <span>第 {{ page }} / {{ pageCount }} 页</span>
      <button data-testid="case-result-next" type="button" :disabled="page === pageCount" @click="page += 1">下一页</button>
    </nav>
  </div>
</template>
