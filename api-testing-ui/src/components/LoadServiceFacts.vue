<script setup lang="ts">
import { computed } from 'vue'
import { factDefinitions, serviceFacts, serviceFactsIssue, sourceLabels, stateLabels, type ServiceFact, type FactState } from '../utils/loadServiceFacts'
const props = defineProps<{modelValue:Record<string,unknown>; readOnly?:boolean; serviceName?:string}>()
const emit = defineEmits<{'update:modelValue':[value:Record<string,unknown>]}>()
const facts = computed(()=>serviceFacts(props.modelValue))
const issue = computed(()=>serviceFactsIssue(props.modelValue))
type Definition = typeof factDefinitions[number]
function row(def:Definition):ServiceFact | undefined { return facts.value?.[def.group]?.find(item=>(item.key || item.kind)===def.key) }
function update(def:Definition, field:string, value:unknown) {
  if (props.readOnly) return
  const old = row(def)
  let next:ServiceFact = {...old, [def.group==='components'?'key':'kind']:def.key, state:old?.state || 'unknown'}
  if (field === 'state') {
    next = {[def.group==='components'?'key':'kind']:def.key,state:value as FactState,...(old?.source?{source:{...old.source}}:{})}
  } else if (field === 'source-kind' || field === 'reference') {
    next.source = {...old?.source, kind:old?.source?.kind || '',reference:old?.source?.reference || '',[field==='source-kind'?'kind':'reference']:value}
  } else next[field] = value
  const current = facts.value || {version:1,components:[],behaviors:[]}
  const entries = current[def.group].filter(item=>(item.key || item.kind)!==def.key)
  emit('update:modelValue',{...props.modelValue,load_service_facts:{...current,[def.group]:[...entries,next]}})
}
function text(event:Event) { return (event.target as HTMLInputElement).value }
function parameterText(item:ServiceFact) {
  return [item.limit != null ? `上限 ${item.limit}` : '',item.duration_ms != null ? `${item.duration_ms} 毫秒` : '',item.bytes != null ? `${item.bytes} 字节` : '',item.ttl_seconds != null ? `TTL ${item.ttl_seconds} 秒` : '',typeof item.refresh_on_request==='boolean' ? `请求时${item.refresh_on_request?'刷新':'不刷新'} TTL` : '',Array.isArray(item.endpoints)?item.endpoints.join('、'):''].filter(Boolean).join(' · ')
}
</script>
<template>
  <details class="service-facts" data-testid="load-service-facts">
    <summary>{{ serviceName ? `${serviceName} · ` : '' }}服务事实（可选） · {{ facts ? '已有记录' : '未记录' }}</summary>
    <p class="facts-note">用于约束性能诊断。来源均由操作人记录，不代表平台自动验证；没有记录的项目按未知处理。保存环境新版本后生效。</p>
    <p v-if="issue && !readOnly" class="facts-error" role="status">{{ issue }}</p>
    <div class="facts-grid">
      <section v-for="def in factDefinitions" :key="def.key" :data-fact="def.key" class="fact-row">
        <template v-if="readOnly">
          <strong>{{ def.label }}：{{ row(def) ? stateLabels[row(def)!.state] : '未记录（未知）' }}</strong>
          <p v-if="row(def)?.source">人工记录来源：{{ sourceLabels[row(def)!.source!.kind] || '未记录' }} · {{ row(def)!.source!.reference }}</p>
          <p v-if="row(def)?.state === 'present' && def.group === 'behaviors'">{{ parameterText(row(def)!) }}</p>
        </template>
        <template v-else>
          <label>{{ def.label }}<select data-field="state" :value="row(def)?.state || 'unknown'" @change="update(def,'state',text($event))"><option value="unknown">{{ row(def) ? '未知' : '未记录（未知）' }}</option><option value="present">存在</option><option value="absent">不存在</option></select></label>
          <template v-if="row(def) && (row(def)!.state !== 'unknown' || row(def)!.source)">
            <label>人工记录来源<select data-field="source-kind" :value="row(def)?.source?.kind || ''" @change="update(def,'source-kind',text($event))"><option value="">请选择来源</option><option v-for="(label,key) in sourceLabels" :key="key" :value="key">{{ label }}</option></select></label>
            <label>引用说明<input data-field="reference" :value="row(def)?.source?.reference || ''" maxlength="500" placeholder="代码位置、版本或观察记录" @input="update(def,'reference',text($event))" /></label>
          </template>
          <template v-if="def.group === 'behaviors' && row(def)?.state === 'present'">
            <label v-if="def.key.includes('slots')">槽位上限<input data-field="limit" type="number" min="1" max="1000000" :value="row(def)?.limit" @input="update(def,'limit',Number(text($event)))" /></label>
            <label v-if="def.key === 'cpu_wall_time_work'">工作时长（毫秒）<input data-field="duration_ms" type="number" min="1" max="3600000" :value="row(def)?.duration_ms" @input="update(def,'duration_ms',Number(text($event)))" /></label>
            <template v-if="def.key === 'shared_ttl_allocation'">
              <label>共享分配大小（字节）<input data-field="bytes" type="number" min="1" max="1099511627776" :value="row(def)?.bytes" @input="update(def,'bytes',Number(text($event)))" /></label>
              <label>TTL（秒）<input data-field="ttl_seconds" type="number" min="1" max="86400" :value="row(def)?.ttl_seconds" @input="update(def,'ttl_seconds',Number(text($event)))" /></label>
              <label>请求时刷新 TTL<select data-field="refresh_on_request" :value="row(def)?.refresh_on_request == null ? '' : String(row(def)?.refresh_on_request)" @change="update(def,'refresh_on_request',text($event)===''?undefined:text($event)==='true')"><option value="">请选择</option><option value="true">刷新</option><option value="false">不刷新</option></select></label>
            </template>
            <label v-if="!def.key.includes('slots')">适用接口路径（每行一条）<textarea data-field="endpoints" rows="2" :value="(row(def)?.endpoints as string[] || []).join('\n')" placeholder="/demo/cpu" @change="update(def,'endpoints',text($event).split('\n').map(value=>value.trim()).filter(Boolean))" /></label>
          </template>
        </template>
      </section>
    </div>
  </details>
</template>
<style scoped>
.service-facts{margin-top:12px;padding:10px 12px;border:1px solid #dce5eb;border-radius:8px;font-size:13px;min-width:0}.service-facts summary{cursor:pointer;font-weight:600;overflow-wrap:anywhere}.facts-note,.fact-row p{color:#596a7b;line-height:1.6;margin:8px 0}.facts-error{color:#a12626}.facts-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,260px),1fr));gap:10px}.fact-row{padding:10px;border:1px solid #e3e9ed;border-radius:6px;min-width:0;overflow-wrap:anywhere}.fact-row label{display:grid;gap:5px;margin:6px 0}.fact-row input,.fact-row select,.fact-row textarea{box-sizing:border-box;width:100%;min-width:0;padding:7px;border:1px solid #cdd8df;border-radius:5px;font:inherit;background:white}.fact-row textarea{resize:vertical}
</style>
