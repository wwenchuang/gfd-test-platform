<script setup lang="ts">
import { computed } from 'vue'
const props = defineProps<{before?:Record<string,unknown>;after:Record<string,unknown>}>()
type Stage = {duration_seconds:number;target:number}
const oldStages = computed(() => (props.before?.stages || []) as Stage[])
const newStages = computed(() => (props.after.stages || []) as Stage[])
function unit(w?:Record<string,unknown>) { return String(w?.executor).includes('arrival-rate') ? w?.time_unit === '1m' ? '次/分钟' : '次/秒' : 'VU' }
function start(w?:Record<string,unknown>) { return w?.start_rate ?? w?.start_vus ?? '未记录' }
</script>
<template>
  <section v-if="newStages.length" class="curve-comparison" aria-label="阶段参数前后对比">
    <h4>完整阶段参数 · 原曲线与建议</h4>
    <p>起始压力：{{ start(before) }} {{ unit(before) }} → {{ start(after) }} {{ unit(after) }}</p>
    <div class="curve-table"><table><thead><tr><th>阶段</th><th>原压力 → 建议压力</th><th>原时长 → 建议时长</th></tr></thead><tbody><tr v-for="(stage,index) in newStages" :key="index"><td>{{ index+1 }}</td><td>{{ oldStages[index]?.target ?? '未记录' }} {{ unit(before) }} → {{ stage.target }} {{ unit(after) }}</td><td>{{ oldStages[index]?.duration_seconds ?? '未记录' }} → {{ stage.duration_seconds }} 秒</td></tr></tbody></table></div>
    <p>阶段目标是该段结束时的压力；相邻目标相同为保持压力，目标降低为降压恢复观察。创建草稿前仍需核对节点与预检。</p>
  </section>
</template>
<style scoped>
.curve-comparison{margin:12px 0;padding:14px;border:1px solid #cedee2;border-radius:8px;background:#f8fbfc;font-size:13px;line-height:1.6}.curve-table{overflow-x:auto}table{width:100%;border-collapse:collapse}th,td{padding:8px;text-align:left;border-bottom:1px solid #dce5e8}h4,p{margin:4px 0 10px}
</style>
