<script setup lang="ts">
import { computed } from 'vue'

type Summary = {
  total?: number
  passed?: number
  failed?: number
  broken?: number
  cancelled?: number
  skipped?: number
}

const props = withDefaults(defineProps<{ summary: Summary; durationMs: number; environmentName: string; conclusion?: string; compact?: boolean }>(), {
  conclusion: '',
  compact: false,
})
const items = computed(() => [
  ['PASSED', '通过', props.summary.passed || 0],
  ['FAILED', '断言失败', props.summary.failed || 0],
  ['BROKEN', '运行异常', props.summary.broken || 0],
  ['CANCELLED', '已取消', props.summary.cancelled || 0],
  ['SKIPPED', '已跳过', props.summary.skipped || 0],
] as const)
const duration = computed(() => props.durationMs >= 1000 ? `${(props.durationMs / 1000).toFixed(2)} 秒` : `${props.durationMs} ms`)
const passRate = computed(() => props.summary.total ? Math.round(((props.summary.passed || 0) / props.summary.total) * 100) : 0)
</script>

<template>
  <section class="report-summary">
    <div class="report-context"><strong>{{ environmentName || '未命名环境' }}</strong><span>总耗时 {{ duration }}</span><span>共 {{ summary.total || 0 }} 条用例</span></div>
    <div v-if="compact" class="compact-summary"><b v-if="conclusion">{{ conclusion }}</b><strong>通过率 {{ passRate }}%</strong><span>{{ summary.passed || 0 }} 通过</span><span>{{ summary.failed || 0 }} 失败</span><span>{{ summary.broken || 0 }} 异常</span><span>{{ summary.cancelled || 0 }} 取消</span><span>{{ summary.skipped || 0 }} 跳过</span></div>
    <div v-else class="summary-grid"><div v-for="([status, label, count]) in items" :key="status" :data-status="status" :class="`summary-${status.toLowerCase()}`"><strong :data-testid="`${status.toLowerCase()}-count`">{{ count }}</strong><span>{{ label }}</span></div></div>
  </section>
</template>
