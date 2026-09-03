<script setup lang="ts">
import { computed } from 'vue'

const props = defineProps<{ series: Array<Record<string, unknown>>; missingWindows?: number }>()
const points = computed(() => props.series.map((item, index) => ({
  index, time: String(item.started_at || ''), requests: Number(item.requests || 0), p95: Number(item.p95_ms || 0),
})))
const max = computed(() => Math.max(1, ...points.value.map(item => item.p95)))
const polyline = computed(() => points.value.map((item, index) => {
  const x = points.value.length <= 1 ? 0 : index * 100 / (points.value.length - 1)
  return `${x},${100 - item.p95 * 100 / max.value}`
}).join(' '))
</script>

<template>
  <section class="load-chart" aria-label="P95响应时间趋势">
    <header><div><h3>P95 响应时间趋势</h3><p>每个点对应一个 5 秒证据窗口。</p></div><span v-if="missingWindows" class="status-pill status-warning">缺失 {{ missingWindows }} 个窗口</span></header>
    <svg v-if="points.length" viewBox="0 0 100 100" role="img" aria-label="P95响应时间折线图" preserveAspectRatio="none"><polyline :points="polyline" fill="none" vector-effect="non-scaling-stroke" /></svg>
    <p v-else class="compact-empty">还没有可绘制的指标窗口。</p>
    <details v-if="points.length"><summary>查看无障碍数据表</summary><div class="load-chart-table"><table><thead><tr><th>时间</th><th>请求数</th><th>P95 毫秒</th></tr></thead><tbody><tr v-for="item in points" :key="`${item.time}-${item.index}`"><td>{{ item.time }}</td><td>{{ item.requests }}</td><td>{{ item.p95 }}</td></tr></tbody></table></div></details>
  </section>
</template>
