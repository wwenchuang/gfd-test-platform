<script setup lang="ts">
import {computed,ref,watch} from 'vue'
import LoadConditionValue from './LoadConditionValue.vue'
import type {LoadRun} from '../api/contracts'
import {loadRunComparisonApi,type RunComparison} from '../api/loadRunComparison'
const props=defineProps<{runs:LoadRun[];projectId?:string}>()
const selected=ref<string[]>([]),search=ref(''),busy=ref(false),error=ref('')
const result=ref<RunComparison>()
let request=0
const available=computed(()=>props.runs.filter(run=>run.state==='finished' && run.finished_at && (!props.projectId || run.project_id===props.projectId)))
const filtered=computed(()=>available.value.filter(run=>`${name(run)} ${run.id} ${String((run.configuration.test_context as Record<string,unknown>|undefined)?.release||'')}`.toLowerCase().includes(search.value.trim().toLowerCase())).slice(0,30))
function name(run:LoadRun):string { return String((run.configuration.scenario as Record<string,unknown>|undefined)?.name || run.id) }
function invalidate():void {request++;result.value=undefined;error.value='';busy.value=false}
watch(()=>props.projectId,()=>{selected.value=[];search.value='';invalidate()})
watch(()=>available.value.map(run=>run.id).join(','),()=>{selected.value=selected.value.filter(id=>available.value.some(run=>run.id===id))})
watch(()=>selected.value.join(','),invalidate)
function toggle(id:string,checked:boolean):void { if(checked && selected.value.length<5) selected.value=[...selected.value,id]; else if(!checked) selected.value=selected.value.filter(value=>value!==id) }
function reference(id:string):void {selected.value=[id,...selected.value.filter(value=>value!==id)]}
async function compare():Promise<void> {
  if(selected.value.length<2 || selected.value.length>5 || busy.value)return
  const seq=++request;busy.value=true;error.value=''
  try{const response=await loadRunComparisonApi.compare([...selected.value]);if(seq===request)result.value=response}
  catch(e){if(seq===request)error.value=e instanceof Error?e.message:'对比读取失败，请重试'}
  finally{if(seq===request)busy.value=false}
}
function number(value:number|null|undefined,unit=''):string{return value==null || !Number.isFinite(value)?'—':`${value.toLocaleString('zh-CN',{maximumFractionDigits:3})}${unit ? ' '+unit : ''}`}
function runLabel(id:string):string {const row=result.value?.runs.find(run=>run.run_id===id);return row?`${row.scenario_name} · ${String(row.conditions.release||'版本未记录')} · ${id.slice(0,8)}`:id.slice(0,8)}
const conditionLabels:Record<string,string>={environment:'环境版本',scenario:'场景内容版本',purpose:'测试目的',release:'业务版本',data_profile:'数据与账号规模',cache_state:'缓存状态',workload:'负载参数',thresholds:'验收标准',dataset:'数据集快照',agents:'压力机与分配规格',monitoring:'监控范围',target_resources:'实测资源分母',stop_policy:'自动停止策略'}
</script>
<template>
<section class="run-comparison" aria-label="多轮性能对比">
  <header><div><h2>多轮性能对比与接口覆盖</h2><p>选择同一项目的 2–5 轮已完成执行。首先核对条件；未记录或证据不足时仅展示原始数值。</p></div></header>
  <label class="comparison-search">搜索已完成执行<input v-model="search" type="search" placeholder="场景、版本或执行编号" /></label>
  <p v-if="!available.length">当前没有可供对比的已完成执行。</p>
  <div v-else class="comparison-options">
    <label v-for="run in filtered" :key="run.id"><input type="checkbox" :data-testid="`comparison-select-${run.id}`" :checked="selected.includes(run.id)" :disabled="!selected.includes(run.id) && selected.length>=5" @change="toggle(run.id,($event.target as HTMLInputElement).checked)" /><span><strong>{{ name(run) }}</strong><small>{{ String((run.configuration.test_context as Record<string,unknown>|undefined)?.release || '版本未记录') }} · {{ new Date(run.finished_at!).toLocaleString() }} · {{ run.id.slice(0,8) }}</small></span></label>
  </div>
  <p v-if="available.length>30">列表最多显示 30 轮，请搜索缩小范围；已选项会保留。</p>
  <div class="comparison-actions"><span>已选 {{ selected.length }} / 5</span><label v-if="selected.length"><span class="reference-caption">参照执行</span><select :value="selected[0]" @change="reference(($event.target as HTMLSelectElement).value)"><option v-for="id in selected" :key="id" :value="id">{{ name(available.find(run=>run.id===id)!) }} · {{ id.slice(0,8) }}</option></select></label><button type="button" class="primary-command" data-testid="compare-runs" :disabled="selected.length<2 || busy" @click="compare">{{ busy?'正在核对证据…':'生成多轮对比' }}</button></div>
  <p v-if="error" role="alert">{{ error }}</p>
  <template v-if="result">
    <p>{{ result.notice }}</p>
    <details class="comparison-conditions"><summary>查看每轮环境、版本、数据、缓存与资源条件</summary><div class="comparison-scroll"><table><thead><tr><th>条件</th><th v-for="run in result.runs" :key="run.run_id">{{ runLabel(run.run_id) }}</th></tr></thead><tbody><tr v-for="(label,key) in conditionLabels" :key="key"><th>{{ label }}</th><td v-for="run in result.runs" :key="run.run_id"><LoadConditionValue :value="run.conditions[key]" :kind="String(key)" /></td></tr></tbody></table></div></details>
    <article v-for="pair in result.comparisons" :key="pair.run_id" class="comparison-pair"><header><h3>{{ runLabel(pair.run_id) }} 对比 {{ runLabel(pair.reference_run_id) }}</h3><b>{{ pair.eligible?'记录条件可比':'条件或证据不足' }}</b></header><p>{{ pair.scope }}</p><ul v-if="pair.reasons.length"><li v-for="reason in pair.reasons" :key="reason">{{ reason }}</li></ul>
      <div v-if="pair.differences.length" class="comparison-scroll"><table><thead><tr><th>差异项</th><th>参照</th><th>当前</th><th>判断</th></tr></thead><tbody><tr v-for="difference in pair.differences" :key="difference.key"><th>{{ difference.label }}</th><td><LoadConditionValue :value="difference.reference" :kind="difference.key" /></td><td><LoadConditionValue :value="difference.current" :kind="difference.key" /></td><td>{{ difference.kind==='variable'?'本次比较变量':difference.kind==='unknown'?'未完整记录':'条件不同' }}</td></tr></tbody></table></div>
      <div class="comparison-scroll"><table><thead><tr><th>指标</th><th>参照</th><th>当前</th><th>当前 − 参照</th><th>相对变化</th></tr></thead><tbody><tr v-for="metric in pair.metrics" :key="metric.key"><th>{{ metric.label }}</th><td>{{ number(metric.reference,metric.unit) }}</td><td>{{ number(metric.current,metric.unit) }}</td><td>{{ number(metric.delta,metric.unit==='%'?'百分点':metric.unit) }}</td><td>{{ number(metric.change_percent,'%') }}</td></tr></tbody></table></div>
    </article>
    <article class="comparison-coverage" aria-label="实际接口覆盖"><h3>实际请求接口覆盖</h3><p>{{ result.coverage.scope }}</p><div class="coverage-totals"><span>执行轮数 <b>{{ result.coverage.selected_run_count }}</b></span><span>已归属接口 <b>{{ result.coverage.observed_endpoint_count }}</b></span><span>当前接口资产 <b>{{ result.coverage.asset_endpoint_count }}</b></span><span>覆盖率 <b>{{ result.coverage.coverage_ratio==null?'未知':number(result.coverage.coverage_ratio*100,'%') }}</b></span></div><p>未归属步骤轮次 {{ result.coverage.unmapped_step_run_count }} · 历史接口版本步骤轮次 {{ result.coverage.historical_endpoint_step_run_count }}</p><p>{{ result.coverage.notice }}</p><p v-if="result.coverage.endpoint_details_truncated">接口明细仅列前 200 项，汇总包含全部已归属接口。</p><details v-if="result.coverage.endpoints.length"><summary>查看已归属接口与执行轮数</summary><div class="comparison-scroll"><table><thead><tr><th>接口</th><th>执行轮数</th><th>已收请求数</th></tr></thead><tbody><tr v-for="endpoint in result.coverage.endpoints" :key="endpoint.endpoint_id"><td>{{ endpoint.method }} {{ endpoint.path }}</td><td>{{ endpoint.run_ids.length }}</td><td>{{ endpoint.requests }}</td></tr></tbody></table></div></details></article>
  </template>
</section>
</template>
<style scoped>
.run-comparison{display:grid;gap:14px;background:#fff;border:1px solid #dbe3ee;border-radius:12px;padding:20px;margin:16px 0;color:#172b43;min-width:0}.run-comparison h2{font-size:20px;margin:0}.run-comparison h3{font-size:16px;margin:0}.run-comparison p,.run-comparison li{font-size:13px;line-height:1.6;color:#617187;margin:6px 0}.run-comparison header{display:flex;justify-content:space-between;gap:12px;align-items:center}.comparison-search{display:grid;gap:6px;font-size:13px;max-width:480px}.comparison-search input,.comparison-actions select{padding:8px;min-height:36px;border:1px solid #cbd5e1;border-radius:6px;box-sizing:border-box;max-width:100%}.comparison-options{display:grid;grid-template-columns:repeat(auto-fit,minmax(250px,1fr));gap:8px;max-height:230px;overflow:auto}.comparison-options>label{display:flex;align-items:center;gap:10px;border:1px solid #e2e8f0;padding:10px;border-radius:6px}.comparison-options input[type=checkbox]{width:18px;height:18px;min-width:18px;flex:0 0 18px;margin:0}.comparison-options strong{font-size:13px}.comparison-options small{display:block;font-size:12px;color:#64748b}.comparison-actions{display:flex;align-items:center;gap:14px;flex-wrap:wrap;font-size:13px}.comparison-actions label{display:flex;align-items:center;gap:8px;min-width:0}.reference-caption{flex:none;white-space:nowrap}.comparison-actions label{max-width:100%}.comparison-actions select{width:320px;min-width:0;flex:1}.comparison-scroll{overflow:auto;margin-top:10px}.comparison-scroll table{width:100%;border-collapse:collapse;text-align:left;font-size:13px}.comparison-scroll td,.comparison-scroll th{padding:10px;border-bottom:1px solid #e2e8f0;vertical-align:top;min-width:100px;max-width:360px;overflow-wrap:anywhere;white-space:normal}.comparison-scroll th{background:#f8fafc}.comparison-conditions td{min-width:230px}.comparison-pair,.comparison-coverage{border-top:1px solid #e2e8f0;padding-top:16px}.comparison-pair header>b{font-size:13px;white-space:nowrap;color:#176da5}.coverage-totals{display:flex;gap:20px;flex-wrap:wrap;font-size:13px;margin:14px 0}.coverage-totals b{font-size:20px;font-variant-numeric:tabular-nums;margin-left:5px}.run-comparison summary{cursor:pointer;color:#176da5;font-size:13px}.run-comparison [role=alert]{color:#b91c1c}@media(max-width:700px){.run-comparison{padding:14px}.run-comparison header{align-items:flex-start;flex-direction:column}.comparison-actions label{flex-wrap:wrap}}
</style>
