<script setup lang="ts">
import type { ExecutionCaseResult } from '../api/contracts'

defineProps<{ results: ExecutionCaseResult[]; activeId?: string }>()
const emit = defineEmits<{ select: [result: ExecutionCaseResult] }>()
</script>

<template>
  <div class="case-result-list"><button v-for="result in results" :key="result.execution_case_id" type="button" :class="{ active: result.execution_case_id === activeId }" @click="emit('select', result)"><span :class="['status-dot', `dot-${result.status.toLowerCase()}`]" /><span><strong>{{ result.case_name || result.endpoint_summary || result.path }}</strong><small>{{ result.method }} {{ result.path }}</small></span><b :class="`status-${result.status.toLowerCase()}`">{{ result.status }}</b><time>{{ result.duration_ms }} ms</time></button></div>
</template>
