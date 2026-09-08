<script setup lang="ts">
import {computed} from 'vue'
const props=defineProps<{value:unknown;kind:string}>()
const structured=computed(()=>props.value!==null && typeof props.value==='object')
const labels:Record<string,string>={'constant-vus':'固定并发','ramping-vus':'阶梯并发','constant-arrival-rate':'固定吞吐','ramping-arrival-rate':'阶梯吞吐',smoke:'流程冒烟',load:'负载测试',stress:'压力测试',soak:'稳定性测试',cold:'冷缓存',warm:'热缓存'}
const summary=computed(()=>{
 const value=props.value
 if(value==null || value==='')return '未记录 / 未核验'
 if(!structured.value)return labels[String(value)] || String(value)
 if(props.kind==='workload' && !Array.isArray(value)){
  const w=value as Record<string,unknown>
  const parts=[labels[String(w.executor)] || '负载配置']
  if(w.rate!=null)parts.push(`${w.rate} 次/${w.time_unit==='1m'?'分钟':w.time_unit==='1s'?'秒':String(w.time_unit||'未记录时间单位')}`)
  if(w.vus!=null)parts.push(`${w.vus} VU`)
  if(w.max_vus!=null)parts.push(`最大 ${w.max_vus} VU`)
  if(w.duration_seconds!=null)parts.push(`${w.duration_seconds} 秒`)
  if(Array.isArray(w.stages))parts.push(`${w.stages.length} 个阶段（展开查看）`)
  return parts.join(' · ')
 }
 if(props.kind==='agents' && Array.isArray(value))return value.map((a)=>{
  const allocation=a.allocation || {}
  return [a.name || `节点 ${String(a.id||'未记录').slice(0,8)}`,`Agent ${a.agent_version||'版本未记录'}`,allocation.vus!=null?`${allocation.vus} VU`:null].filter(Boolean).join(' · ')
 }).join('\n') || '未记录节点'
 return Array.isArray(value)?`已记录 ${value.length} 项配置`:'已记录配置'
})
</script>
<template>
 <div class="condition-value"><span data-testid="condition-summary">{{ summary }}</span><details v-if="structured"><summary>查看原始配置</summary><pre>{{ JSON.stringify(value,null,2) }}</pre></details></div>
</template>
<style scoped>
.condition-value{font-size:13px;line-height:1.6;white-space:pre-line;overflow-wrap:anywhere}.condition-value details{margin-top:5px}.condition-value summary{cursor:pointer;color:#176da5;font-size:12px}.condition-value pre{max-height:220px;overflow:auto;white-space:pre-wrap;font-size:12px;padding:8px;background:#f5f8fc;border-radius:6px}
</style>
