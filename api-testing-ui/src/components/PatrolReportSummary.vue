<script setup lang="ts">
import { computed } from 'vue'
import type { ExecutionView } from '../api/contracts'

const props = defineProps<{ execution: ExecutionView }>()
type Patrol = { message?: string; 调查状态?: string; 结论?: string; 下一步?: string }
const patrol = computed(() => props.execution.summary?.agent_patrol as unknown as Patrol | undefined)
const printHints = computed(() => [...new Set(props.execution.case_results.map(row => row.reprint_hint)
  .filter((hint): hint is string => Boolean(hint && !patrol.value?.结论?.includes(hint))))])
const states: Record<string, string> = {
  completed: '调查已完成', limited: '调查受限', interrupted: '调查中断',
  failed: '调查失败', cancelled: '调查已取消', '本次无失败': '本次无失败',
}
</script>

<template>
  <section v-if="patrol" class="report-section" aria-label="Agent 自动巡检">
    <strong>Agent 自动巡检</strong>
    <span v-if="states[patrol.调查状态 || '']"> · {{ states[patrol.调查状态 || ''] }}</span>
    <p>{{ patrol.message }}</p>
    <p v-if="patrol.结论">{{ patrol.结论 }}</p>
    <p v-for="hint in printHints" :key="hint">{{ hint }}</p>
    <p v-if="patrol.下一步"><strong>建议操作</strong> {{ patrol.下一步 }}</p>
    <a :href="'#/runs?executionId=' + encodeURIComponent(execution.id)">查看本次巡检的用例证据</a>
  </section>
</template>
