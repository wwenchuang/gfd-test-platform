<script setup lang="ts">
import { computed } from 'vue'

const props = defineProps<{ summary: Record<string, number>; durationMs: number; environmentName: string }>()
const items = computed(() => [
  ['PASSED', '通过', props.summary.passed || 0],
  ['FAILED', '断言失败', props.summary.failed || 0],
  ['BROKEN', '运行异常', props.summary.broken || 0],
  ['CANCELLED', '已取消', props.summary.cancelled || 0],
  ['SKIPPED', '已跳过', props.summary.skipped || 0],
] as const)
const duration = computed(() => props.durationMs >= 1000 ? `${(props.durationMs / 1000).toFixed(2)} 秒` : `${props.durationMs} ms`)
</script>

<template>
  <section class="report-summary">
    <div class="report-context"><strong>{{ environmentName || '未命名环境' }}</strong><span>总耗时 {{ duration }}</span><span>共 {{ summary.total || 0 }} 条用例</span></div>
    <div class="summary-grid"><div v-for="([status, label, count]) in items" :key="status" :data-status="status" :class="`summary-${status.toLowerCase()}`"><strong :data-testid="`${status.toLowerCase()}-count`">{{ count }}</strong><span>{{ label }}</span></div></div>
  </section>
</template>
