<script setup lang="ts">
import { computed, ref } from 'vue'

const props = defineProps<{ series: Array<Record<string, unknown>>; missingWindows?: number; referenceP95?: number }>()
const active = ref<number | null>(null)
function timestamp(value: unknown): number {
  const text = String(value || '')
  return Date.parse(/^\d{2}:\d{2}:\d{2}$/.test(text) ? `1970-01-01T${text}Z` : text)
}
const points = computed(() => props.series.map((item, index) => ({
  index, time: String(item.started_at || ''), stamp: timestamp(item.started_at), requests: Number(item.requests || 0),
  p95: item.p95_ms != null && Number.isFinite(Number(item.p95_ms)) && Number(item.p95_ms) >= 0 && Number(item.requests) > 0 ? Number(item.p95_ms) : null,
})).sort((a, b) => a.stamp - b.stamp))
const max = computed(() => {
  const peak = Math.max(1, props.referenceP95 || 0, ...points.value.map(item => item.p95 ?? 0)) * 1.1
  const magnitude = 10 ** Math.floor(Math.log10(peak / 4))
  const step = [1, 2, 5, 10].find(value => value * magnitude >= peak / 4)! * magnitude
  return step * 4
})
const validTimes = computed(() => points.value.every(item => Number.isFinite(item.stamp)))
function x(index: number): number {
  const first = points.value[0]?.stamp ?? 0
  const last = points.value.at(-1)?.stamp ?? first
  return validTimes.value && last > first ? 2 + (points.value[index]!.stamp - first) / (last - first) * 96 : points.value.length <= 1 ? 50 : 2 + index / (points.value.length - 1) * 96
}
function y(value: number): number { return 96 - value / max.value * 92 }
const segments = computed(() => {
  const groups: string[][] = []
  let current: string[] = []
  points.value.forEach((item, index) => {
    if (item.p95 === null || (index > 0 && item.stamp - points.value[index - 1]!.stamp > 5500)) {
      if (current.length) groups.push(current)
      current = []
    }
    if (item.p95 !== null) current.push(`${x(index)},${y(item.p95)}`)
  })
  if (current.length) groups.push(current)
  return groups.map(group => group.join(' '))
})
const ticks = computed(() => points.value.filter((_, i) => [0, Math.floor((points.value.length - 1) / 2), points.value.length - 1].includes(i)))
const selected = computed(() => active.value === null ? null : points.value[active.value])
function timeLabel(value: string): string {
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? value || '时间未上报' : date.toLocaleTimeString('zh-CN', { hour12: false })
}
</script>

<template>
  <section class="load-chart" aria-label="P95响应时间趋势">
    <header><div><h3>响应速度随时间变化 <span class="chart-tag">P95</span></h3><p>每 5 秒统计一次：约 95% 的请求耗时不超过该值。数值越低，响应越快。</p></div><span v-if="missingWindows" class="status-pill status-warning">缺失 {{ missingWindows }} 个窗口</span></header>
    <template v-if="points.some(item => item.p95 !== null)">
      <div class="chart-unit">响应时间 · 毫秒（1,000 毫秒 = 1 秒）</div>
      <div class="chart-frame">
        <div class="chart-y"><span v-for="fraction in [1, .75, .5, .25, 0]" :key="fraction">{{ (max * fraction).toLocaleString('zh-CN', { maximumFractionDigits: 1 }) }}</span></div>
        <svg viewBox="0 0 100 100" role="img" aria-label="P95响应时间折线图" preserveAspectRatio="none">
          <line v-for="fraction in [0, .25, .5, .75, 1]" :key="fraction" x1="0" x2="100" :y1="y(max * fraction)" :y2="y(max * fraction)" class="chart-gridline" vector-effect="non-scaling-stroke" />
          <line v-if="referenceP95 != null" x1="0" x2="100" :y1="y(referenceP95)" :y2="y(referenceP95)" class="chart-reference" vector-effect="non-scaling-stroke" />
          <polyline v-for="(segment, index) in segments" :key="index" :points="segment" fill="none" vector-effect="non-scaling-stroke" />
          <template v-for="(item, index) in points" :key="item.index"><circle v-if="item.p95 !== null" :cx="x(index)" :cy="y(item.p95)" r=".15" vector-effect="non-scaling-stroke" tabindex="0" :aria-label="`${timeLabel(item.time)}，P95 ${item.p95} 毫秒，${item.requests} 次请求`" @mouseenter="active = index" @focus="active = index" @click="active = index"><title>{{ timeLabel(item.time) }} · {{ item.p95 }} 毫秒 · {{ item.requests }} 次请求</title></circle></template>
        </svg>
      </div>
      <p v-if="referenceP95 != null" class="chart-reference-label">橙色虚线：全程 P95 要求 ≤ {{ referenceP95 }} 毫秒（供趋势对照，最终以全程统计判定）。</p>
      <div class="chart-x"><span v-for="item in ticks" :key="item.index">{{ timeLabel(item.time) }}</span></div>
      <p class="chart-readout" aria-live="polite">{{ selected ? `${timeLabel(selected.time)} · P95 ${selected.p95} 毫秒 · ${selected.requests} 次请求` : '悬停、点击或用 Tab 选中数据点，查看该时段数值。时间按浏览器本地时区显示。' }}</p>
    </template>
    <p v-else class="compact-empty">暂无响应时间样本，不能据此判断响应速度。</p>
    <p class="chart-note">断线表示缺少采样数据；各时段 P95 不能直接平均成全程 P95。分位数由耗时分桶估算，折线平台不代表每次请求耗时完全相同。</p>
    <details v-if="points.length"><summary>查看每 5 秒的详细数据（{{ points.length }} 条）</summary><div class="load-chart-table"><table><thead><tr><th>采样时间</th><th>请求数</th><th>P95（毫秒）</th></tr></thead><tbody><tr v-for="item in points" :key="item.index"><td>{{ timeLabel(item.time) }}</td><td>{{ item.requests }}</td><td>{{ item.p95 ?? '暂无样本' }}</td></tr></tbody></table></div></details>
  </section>
</template>

<style scoped>
.load-chart > header p, .chart-note, .chart-readout { font-size: 13px; line-height: 1.65; }
.load-chart h3 { font-size: 18px; }.chart-tag { color: #186caa; background: #edf6ff; padding: 3px 8px; font-size: 12px; border-radius: 4px; vertical-align: middle; }
.chart-unit { font-size: 12px; color: #53647b; margin-top: 20px; }.chart-frame { display: grid; grid-template-columns: 58px minmax(0, 1fr); height: 240px; margin-top: 12px; }
.chart-y { display: flex; flex-direction: column; justify-content: space-between; padding: 9px 10px 9px 0; text-align: right; font-size: 12px; color: #53647b; font-variant-numeric: tabular-nums; }
.load-chart svg { height: 240px; margin: 0; padding: 0; border: 0; background: #f8fbfe; overflow: visible; }.chart-gridline { stroke: #dce5ef; stroke-dasharray: 4 4; }.load-chart circle { fill: #1977b9; stroke: #1977b9; stroke-width: 5; cursor: crosshair; }.load-chart circle:focus { fill: #e99520; outline: none; }
.chart-reference { stroke: #b76c0b; stroke-width: 1.5; stroke-dasharray: 6 4; }.chart-reference-label { font-size: 12px; color: #8a5511; margin: 8px 0 0 58px; }.chart-x { margin-left: 58px; display: flex; justify-content: space-between; padding: 8px 2% 0; font-size: 12px; color: #53647b; }.chart-readout { color: #24557c; background: #edf6fc; padding: 8px 12px; border-radius: 5px; min-height: 38px; }.chart-note { color: #637187; }.load-chart summary { font-size: 13px; cursor: pointer; font-weight: 600; }.load-chart th, .load-chart td { font-size: 13px; padding: 10px; }
@media(max-width: 620px) { .chart-frame, .load-chart svg { height: 190px; }.load-chart > header { flex-direction: column; }.chart-y { font-size: 11px; } }
</style>
