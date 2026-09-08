<script setup lang="ts">
import { monitoringMetricLabel, monitoringScopeLabel } from '../utils/monitoringMetrics'
import { computed, ref, watch } from 'vue'
import EnvironmentMonitoringPanel from './EnvironmentMonitoringPanel.vue'
import { monitoringApi, type MonitoringSelection, type MonitoringService } from '../api/monitoring'
import { apiTestingHasPermission } from '../utils/authRedirect'
const props = defineProps<{ environmentRevisionId: string; modelValue: MonitoringSelection }>()
const emit = defineEmits<{ 'update:modelValue': [value: MonitoringSelection] }>()
const items = ref<MonitoringService[]>([])
const loading = ref(false)
const error = ref('')
const cleared = ref(false)
const configuring = ref(false)
const checking = ref('')
const canCheck = computed(() => apiTestingHasPermission('api.environment') || apiTestingHasPermission('api.loadtest.execute'))
const canManage = computed(() => apiTestingHasPermission('api.environment'))
let request = 0
async function refresh(): Promise<void> {
  const seq = ++request
  loading.value = true; error.value = ''
  try {
    const result = props.environmentRevisionId ? await monitoringApi.list(props.environmentRevisionId) : []
    if (seq !== request) return
    items.value = result.filter(item => item.status === 'active')
    emit('update:modelValue', { ...props.modelValue, services: props.modelValue.services.filter(selected => items.value.some(item => item.revision_id === selected.revision_id)) })
  } catch (e) { if (seq === request) error.value = e instanceof Error ? e.message : '监控配置读取失败' }
  finally { if (seq === request) loading.value = false }
}
watch(() => props.environmentRevisionId, (_, previous) => {
  cleared.value = Boolean(previous && props.modelValue.services.length)
  items.value = []; configuring.value = false
  emit('update:modelValue', { services: [], before_seconds: 60, after_seconds: 60 })
  void refresh()
}, { immediate: true })
async function check(item: MonitoringService): Promise<void> {
  if (!canCheck.value) return
  checking.value = item.id; error.value = ''
  const scope = props.environmentRevisionId
  try { const result = await monitoringApi.check(item.id); if (scope === props.environmentRevisionId) item.last_check = result }
  catch (e) { if (scope === props.environmentRevisionId) error.value = e instanceof Error ? e.message : '连接检查失败' }
  finally { checking.value = '' }
}
function toggle(item: MonitoringService, checked: boolean): void {
  const services = props.modelValue.services.filter(selected => selected.revision_id !== item.revision_id)
  if (checked) services.push({ revision_id: item.revision_id, required: false })
  emit('update:modelValue', { ...props.modelValue, services })
}
function required(id: string, value: boolean): void { emit('update:modelValue', { ...props.modelValue, services: props.modelValue.services.map(item => item.revision_id === id ? { ...item, required: value } : item) }) }
function windowValue(key: 'before_seconds' | 'after_seconds', event: Event): void {
  const value = Number((event.target as HTMLInputElement).value)
  emit('update:modelValue', { ...props.modelValue, [key]: value })
}
const selected = (id: string) => props.modelValue.services.find(item => item.revision_id === id)
</script>
<template>
  <section class="monitoring-selection" aria-label="本次服务监控">
    <header><div><h3>服务监控（可选）</h3><p>选择本次观测的资源范围与指标；整机、容器与 Pod 内各容器按配置分别展示。</p></div><button v-if="canManage" type="button" class="secondary-command" :disabled="!environmentRevisionId" @click="configuring = !configuring">{{ configuring ? '收起配置' : '新增 / 配置监控服务' }}</button></header>
    <p v-if="cleared" role="status">环境已切换，已清除上个环境的监控选择。</p>
    <p v-if="loading" role="status">正在读取当前环境监控…</p>
    <p v-else-if="error" role="alert">{{ error }} <button type="button" class="secondary-command" @click="refresh">重试</button></p>
    <p v-else-if="!items.length">当前环境暂无可用监控；可新增配置，或不附加资源监控继续创建草稿。</p>
    <article v-for="item in items" :key="item.id" class="monitoring-choice">
      <label><input :data-testid="`monitor-select-${item.id}`" type="checkbox" :checked="Boolean(selected(item.revision_id))" @change="toggle(item, ($event.target as HTMLInputElement).checked)" /><span><strong>{{ item.name }}</strong><small>{{ Object.values(item.labels).join(' / ') }} · {{ monitoringScopeLabel(item.deployment) }} · {{ item.metrics.map(monitoringMetricLabel).join('、') }}</small><small>{{ item.last_check?.message || '尚未检查；启动前将由后台验证' }}</small></span></label>
      <button v-if="canCheck" type="button" class="secondary-command" :disabled="Boolean(checking)" @click="check(item)">{{ checking === item.id ? '检查中…' : '测试连接' }}</button>
      <label v-if="selected(item.revision_id)"><input :data-testid="`monitor-required-${item.id}`" type="checkbox" :checked="selected(item.revision_id)?.required" @change="required(item.revision_id, ($event.target as HTMLInputElement).checked)" />本次必选</label>
    </article>
    <template v-if="modelValue.services.length"><p>已选 {{ modelValue.services.length }} 项，其中 {{ modelValue.services.filter(item => item.required).length }} 项必选。必选监控未接通会阻止正式启动；可选缺失会提示资源监控证据不足，不单独阻止性能阈值判定。</p><div class="monitoring-windows"><label>前观察窗口（秒）<input :value="modelValue.before_seconds" data-testid="monitor-before" type="number" min="0" max="600" @input="windowValue('before_seconds', $event)" /></label><label>后观察窗口（秒）<input :value="modelValue.after_seconds" data-testid="monitor-after" type="number" min="0" max="600" @input="windowValue('after_seconds', $event)" /></label></div><p>压力结束后仍会等待后观察窗口及约 30 秒数据回补，再完成资源监控报告。</p></template>
    <EnvironmentMonitoringPanel v-if="configuring" :environment-revision-id="environmentRevisionId" @changed="refresh" />
  </section>
</template>
<style scoped>
.monitoring-selection{display:grid;gap:12px;margin:20px 0;padding:18px;border:1px solid #e2e8f0;border-radius:8px}.monitoring-selection header,.monitoring-choice{display:flex;justify-content:space-between;align-items:center;gap:12px}.monitoring-selection h3,.monitoring-selection p{margin:0}.monitoring-selection p,.monitoring-choice small{font-size:13px;line-height:1.6;color:#64748b}.monitoring-choice{padding:10px 0;border-bottom:1px solid #e2e8f0}.monitoring-choice label{display:flex;align-items:center;gap:10px}.monitoring-choice small{display:block;overflow-wrap:anywhere}.monitoring-windows{display:flex;gap:16px}.monitoring-windows label{display:grid;gap:6px}.monitoring-windows input{width:120px;padding:8px}.monitoring-selection button{display:inline-flex;white-space:nowrap}.monitoring-selection [role=alert]{color:#b91c1c}@media(max-width:700px){.monitoring-selection header,.monitoring-choice{align-items:flex-start;flex-direction:column}}

.monitoring-choice label{min-width:0;line-height:1.5}
.monitoring-choice label>span{min-width:0}
.monitoring-choice input[type=checkbox]{width:18px;height:18px;min-width:18px;min-height:18px;max-width:18px;max-height:18px;flex:0 0 18px;margin:0;padding:0;accent-color:#176da5}
.monitoring-windows{flex-wrap:wrap}
</style>
