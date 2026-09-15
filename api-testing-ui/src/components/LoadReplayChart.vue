<script setup lang="ts">
import { computed, ref, watch } from 'vue'
interface Point { time: number; value: number }
interface Series { id: string; name: string; unit: string; color: string; points: Point[]; source?: string }
const props = defineProps<{ title: string; unit: string; lines: Series[]; start: number; end: number; cursor: number; windows: number[]; reference?: number | null; maxGap?: number }>()
const emit = defineEmits<{ seek: [time: number] }>()
const hover = ref<number | null>(null)
watch(() => props.cursor, () => { hover.value = null })
const displayTime = computed(() => hover.value ?? props.cursor)
const number = (value: number) => value.toLocaleString('zh-CN', { maximumFractionDigits: 2 })
function elapsed(time: number): string {
  const seconds = Math.max(0, Math.round(time - props.start))
  return `${String(Math.floor(seconds / 60)).padStart(2, '0')}:${String(seconds % 60).padStart(2, '0')}`
}
const span = computed(() => Math.max(1, props.end - props.start))
const x = (time: number) => 64 + Math.max(0, Math.min(1, (time - props.start) / span.value)) * 510
const maximum = computed(() => {
  const max = Math.max(props.reference ?? 0, ...props.lines.flatMap(line => line.points.map(p => p.value)), 0)
  if (!max) return 1
  const magnitude = 10 ** Math.floor(Math.log10(max))
  return Math.ceil(max / magnitude * 2) / 2 * magnitude
})
const y = (value: number) => 210 - value / maximum.value * 170
const ticks = computed(() => Array.from({length: 5}, (_, i) => ({value: maximum.value * i / 4, time: props.start + span.value * i / 4})))
function segments(line: Series): string[] {
  const result: string[] = []
  let current: string[] = []
  let previous: Point | undefined
  const points = line.points.filter(p => p.time >= props.start && p.time <= props.end && Number.isFinite(p.value))
  if (line.id === 'plan' && points.length === 1) points.push({...points[0], time:props.end})
  for (const point of points) {
    if (previous && line.id !== 'plan' && point.time - previous.time > (props.maxGap ?? 5)) {
      result.push(current.join(' ')); current = []
    }
    current.push(`${x(point.time)},${y(point.value)}`)
    previous = point
  }
  if (current.length) result.push(current.join(' '))
  return result
}
function valueAt(line: Series, time: number): Point | null {
  if (line.id === 'plan') {
    const before = [...line.points].reverse().find(p => p.time <= time)
    const after = line.points.find(p => p.time > time)
    if (!before) return null
    return {time, value:after ? before.value + (after.value-before.value)*(time-before.time)/(after.time-before.time) : before.value}
  }
  // Sampling buckets are shown only within their own window, never carried through a gap.
  return line.points.find(p => p.time >= time && p.time < time + 5) ?? null
}
function keydown(event: KeyboardEvent): void {
  if (!['ArrowLeft','ArrowRight','Home','End'].includes(event.key)) return
  event.preventDefault(); event.stopPropagation()
  const index = Math.max(0, props.windows.findIndex(t => t >= props.cursor))
  const next = event.key === 'Home' ? 0 : event.key === 'End' ? props.windows.length-1 : index + (event.key === 'ArrowRight' ? 1 : -1)
  const time = props.windows[Math.min(props.windows.length-1, Math.max(0,next))]
  if (time != null) emit('seek',time)
}
</script>

<template>
  <article class="replay-readable-chart">
    <h3>{{ title }}</h3>
    <div class="chart-legend">
      <span v-for="line in lines" :key="line.id"><i :style="{background:line.color}" />{{ line.name }}</span>
      <span v-if="reference != null" class="threshold-label">验收上限 {{ number(reference) }} {{ unit }}</span>
    </div>
    <svg viewBox="0 0 600 258" tabindex="0" role="group" :aria-label="`${title}，单位${unit}。左右方向键切换窗口`" @keydown="keydown" @mouseleave="hover=null">
      <text x="64" y="20" class="unit-label">{{ unit }}</text>
      <g v-for="tick in ticks" :key="tick.value">
        <line x1="64" x2="574" :y1="y(tick.value)" :y2="y(tick.value)" stroke="#e2e8f0" />
        <text x="55" :y="y(tick.value)+4" text-anchor="end">{{ number(tick.value) }}</text>
        <text :x="x(tick.time)" y="234" text-anchor="middle">{{ elapsed(tick.time) }}</text>
      </g>
      <text x="574" y="253" text-anchor="end">距开始（分:秒）</text>
      <template v-for="line in lines" :key="line.id">
        <polyline v-for="(segment,index) in segments(line)" :key="index" :points="segment" :stroke="line.color" fill="none" stroke-width="2" />
        <circle v-for="point in line.points.filter(p=>p.time>=start && p.time<=end)" :key="point.time" :cx="x(point.time)" :cy="y(point.value)" r="3" :fill="line.color" stroke="white" />
      </template>
      <line v-if="reference != null" data-testid="threshold" x1="64" x2="574" :y1="y(reference)" :y2="y(reference)" stroke="#b45309" stroke-dasharray="6 4" />
      <line data-testid="time-cursor" :x1="x(displayTime)" :x2="x(displayTime)" y1="35" y2="210" stroke="#2563eb" stroke-width="2" stroke-dasharray="4 3" />
      <template v-for="line in lines" :key="`selected-${line.id}`">
        <circle v-if="valueAt(line,displayTime)" :cx="x(valueAt(line,displayTime)!.time)" :cy="y(valueAt(line,displayTime)!.value)" r="5" :fill="line.color" stroke="white" stroke-width="2" />
      </template>
      <rect v-for="(time,index) in windows" :key="time" :data-time="time" :x="x(time)" y="30" :width="Math.max(1,x(Math.min(time+5,windows[index+1]??end))-x(time))" height="184" fill="transparent" class="window-target" @mouseenter="hover=time" @click="hover=null; emit('seek',time)"><title>选择 {{ elapsed(time) }} 的采样窗口</title></rect>
    </svg>
    <div class="sample-detail" role="status" aria-live="polite">
      <strong>{{ hover == null ? '已选窗口' : '预览窗口' }} {{ elapsed(displayTime) }}–{{ elapsed(Math.min(end,displayTime+5)) }}</strong>
      <span v-for="line in lines" :key="line.id"><i :style="{background:line.color}" />{{ line.name }}：<b>{{ valueAt(line,displayTime) ? `${number(valueAt(line,displayTime)!.value)} ${unit}` : '无采样' }}</b></span>
    </div>
    <p class="chart-help">实测圆点是采样值，计划线来自配置。悬停查看，点击联动；蓝色竖线是选中时间，断线表示缺少采样。</p>
    <p v-if="reference != null" class="chart-help">橙色横线是验收上限，供各窗口对照；最终是否达标以全程统计为准。</p>
    <p v-if="!lines.some(line=>line.points.length)">暂无可用监控采样</p>
    <details v-if="lines.some(line=>line.source)"><summary>查看监控来源</summary><p v-for="line in lines" :key="line.id">{{ line.name }}：{{ line.source }}</p></details>
  </article>
</template>

<style scoped>
.replay-readable-chart{min-width:0;padding:14px;border:1px solid #dbe7f3;border-radius:12px;background:#fbfdff}.replay-readable-chart h3{margin:0 0 12px;font-size:16px;color:#172b46}.chart-legend{display:flex;gap:8px 18px;flex-wrap:wrap;min-height:26px;font-size:13px;color:#475569}.chart-legend span,.sample-detail span{display:inline-flex;align-items:center;gap:6px}.chart-legend i,.sample-detail i{display:inline-block;width:10px;height:10px;border-radius:50%;flex-shrink:0}.threshold-label{color:#92400e}.replay-readable-chart svg{width:100%;height:auto;display:block;background:white;border-radius:8px}.replay-readable-chart svg:focus-visible{outline:2px solid #2563eb}.replay-readable-chart svg text{font-size:12px;fill:#52637a;font-family:inherit}.unit-label{font-weight:600}.window-target{cursor:pointer}.sample-detail{display:flex;flex-wrap:wrap;gap:8px 18px;background:#eff6ff;border-left:3px solid #2563eb;border-radius:4px;padding:10px;font-size:13px;min-height:58px}.sample-detail strong{width:100%;color:#1e40af}.chart-help{font-size:12px;color:#64748b;margin:10px 0 0;line-height:1.6}details{margin-top:8px;font-size:12px;color:#64748b;overflow-wrap:anywhere}summary{cursor:pointer}details p{margin:6px 0}
</style>
