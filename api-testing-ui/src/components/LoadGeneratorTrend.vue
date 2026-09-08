<script setup lang="ts">
import { computed } from 'vue'
const props = defineProps<{ title: string; unit: string; samples: Array<Record<string, any>>; field: string; divisor?: number; interval: number }>()
const points = computed(() => props.samples.map(p => ({ t: Date.parse(p.sampled_at), v: typeof p[props.field] === 'number' && Number.isFinite(p[props.field]) ? p[props.field] / (props.divisor || 1) : null })))
const top = computed(() => Math.max(.1, ...points.value.map(p => p.v ?? 0)) * 1.1)
const paths = computed(() => {
 const paths: string[] = []; let group: string[] = []
 const first = points.value[0]?.t || 0; const span = (points.value.at(-1)?.t || first) - first
 points.value.forEach((p, i) => { if(p.v === null || (i && p.t - points.value[i-1]!.t > props.interval * 1500)) { if(group.length) paths.push(group.join(' ')); group=[] } if(p.v !== null) group.push(`${span ? 5 + (p.t-first)/span*590 : 300},${115-p.v/top.value*110}`) })
 if(group.length) paths.push(group.join(' '));return paths
})
const time = (t: number | undefined) => t ? new Date(t).toLocaleTimeString('zh-CN', { hour12:false }) : '—'
</script>
<template><figure class="generator-trend"><figcaption>{{ title }} <small>单位：{{ unit }}</small></figcaption><template v-if="paths.length"><div class="trend-frame"><div class="trend-axis"><span>{{ top.toFixed(1) }}</span><span>{{ (top/2).toFixed(1) }}</span><span>0</span></div><svg viewBox="0 0 600 120" preserveAspectRatio="none" role="img" :aria-label="`${title}，${unit}`"><line v-for="y in [5,60,115]" :key="y" x1="0" x2="600" :y1="y" :y2="y" stroke="#dce5ee"/><polyline v-for="(path,index) in paths" :key="index" :points="path" fill="none" stroke="#176da5" stroke-width="2" vector-effect="non-scaling-stroke"/></svg></div><div class="trend-times"><span>{{ time(points[0]?.t) }}</span><span>{{ time(points.at(-1)?.t) }}</span></div></template><p v-else>暂无有效样本</p><small>按实际时间排列；缺失样本断线。具体数值见下方采样明细。</small></figure></template>
<style scoped>.generator-trend{margin:0;border:1px solid #e2e8f0;border-radius:8px;padding:12px;background:white}.generator-trend figcaption{font-size:14px;font-weight:600;margin-bottom:10px}.generator-trend small{font-size:12px;color:#617086;font-weight:400}.trend-frame{display:grid;grid-template-columns:38px minmax(0,1fr);height:130px;gap:5px}.trend-axis{display:flex;flex-direction:column;justify-content:space-between;font-size:11px;color:#617086;text-align:right}.trend-frame svg{height:130px;width:100%;overflow:visible}.trend-times{display:flex;justify-content:space-between;font-size:11px;color:#617086;margin:8px 0 8px 43px}</style>
