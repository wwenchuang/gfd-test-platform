<script setup lang="ts">
import { computed, reactive, ref, watch } from 'vue'
import { monitoringApi, type MonitoringService } from '../api/monitoring'
import { apiTestingHasPermission } from '../utils/authRedirect'
const props = withDefaults(defineProps<{ environmentRevisionId: string; readonly?: boolean }>(), { readonly: false })
const emit = defineEmits<{ changed: []; saved: [service: MonitoringService] }>()
const items = ref<MonitoringService[]>([])
const loading = ref(false)
const busy = ref(false)
const error = ref('')
const editing = ref(false)
const current = ref<MonitoringService>()
const form = reactive({ name: '', description: '', source_url: '', instance: '', token: '', step_seconds: 15, authorize_host: false, cpu: true, memory: true })
const canCheck = computed(() => apiTestingHasPermission('api.environment') || apiTestingHasPermission('api.loadtest.execute'))
const canManage = computed(() => !props.readonly && apiTestingHasPermission('api.environment'))
const canAuthorize = computed(() => apiTestingHasPermission('platform.configure'))
const valid = computed(() => form.name.trim() && /^https?:\/\//.test(form.source_url) && form.instance.trim() && Number.isInteger(form.step_seconds) && form.step_seconds >= 5 && form.step_seconds <= 300 && (form.cpu || form.memory))
let request = 0
async function refresh(): Promise<void> {
  const seq = ++request
  items.value = []; error.value = ''; loading.value = true
  try { const result = await monitoringApi.list(props.environmentRevisionId); if (seq === request) items.value = result }
  catch (e) { if (seq === request) error.value = e instanceof Error ? e.message : '监控配置读取失败' }
  finally { if (seq === request) loading.value = false }
}
watch(() => props.environmentRevisionId, () => { editing.value = false; form.token = ''; void refresh() }, { immediate: true })
function edit(item?: MonitoringService): void {
  current.value = item
  Object.assign(form, { name: item?.name || '', description: item?.description || '', source_url: item?.source_url || '', instance: item?.labels.instance || '', token: '', step_seconds: item?.step_seconds || 15, authorize_host: false, cpu: item ? item.metrics.includes('cpu_percent') : true, memory: item ? item.metrics.includes('memory_percent') : true })
  editing.value = true; error.value = ''
}
async function save(): Promise<void> {
  if (!canManage.value || !valid.value || busy.value) return
  busy.value = true; error.value = ''
  const scope = props.environmentRevisionId
  try {
    const service = await monitoringApi.save(current.value?.id, { environment_revision_id: scope, name: form.name.trim(), description: form.description.trim(), deployment: 'host', source_url: form.source_url.trim(), labels: { instance: form.instance.trim() }, metrics: [...(form.cpu ? ['cpu_percent'] : []), ...(form.memory ? ['memory_percent'] : [])], step_seconds: form.step_seconds, ...(form.token ? { token: form.token } : {}), ...(canAuthorize.value && form.authorize_host ? { authorize_host: true } : {}) })
    form.token = ''
    if (scope !== props.environmentRevisionId) return
    editing.value = false; await refresh(); emit('saved', service); emit('changed')
  } catch (e) { if (scope === props.environmentRevisionId) error.value = e instanceof Error ? e.message : '保存失败' }
  finally { busy.value = false }
}
async function check(item: MonitoringService): Promise<void> {
  if (!canCheck.value) return
  busy.value = true; error.value = ''
  try { item.last_check = await monitoringApi.check(item.id); emit('changed') }
  catch (e) { error.value = e instanceof Error ? e.message : '连接检查失败' }
  finally { busy.value = false }
}
async function disable(item: MonitoringService): Promise<void> {
  if (!canManage.value) return
  busy.value = true; error.value = ''
  try { await monitoringApi.disable(item.id); await refresh(); emit('changed') }
  catch (e) { error.value = e instanceof Error ? e.message : '停用失败' }
  finally { busy.value = false }
}
</script>
<template>
  <section class="monitoring-panel" aria-label="环境服务与监控">
    <header><div><h3>服务与监控</h3><p>复用环境内的整机 CPU / 内存监控；当前不采集单个服务、容器或 Pod 的资源。</p></div><button v-if="canManage" type="button" class="secondary-command" data-testid="monitoring-create" :disabled="busy || loading" @click="edit()">新增监控服务</button></header>
    <p v-if="error" role="alert">{{ error }}</p>
    <p v-if="loading" role="status">正在读取监控配置…</p>
    <p v-else-if="!items.length">当前环境暂无监控服务。</p>
    <article v-for="item in items" :key="item.id" class="monitoring-row">
      <div><strong>{{ item.name }}</strong><small>{{ item.labels.instance }} · 整机 · {{ item.status === 'active' ? '启用' : '已停用' }} · {{ item.metrics.includes('cpu_percent') ? 'CPU' : '' }} {{ item.metrics.includes('memory_percent') ? '内存' : '' }}</small><small>{{ item.last_check?.message || '尚未检查采集' }}</small><small v-if="item.last_check?.checked_at">最近检查：{{ new Date(item.last_check.checked_at).toLocaleString() }}</small></div>
      <div class="monitoring-actions"><button v-if="canCheck" type="button" class="secondary-command" :disabled="busy || item.status !== 'active'" @click="check(item)">测试连接</button><button v-if="canManage" type="button" class="secondary-command" :disabled="busy" @click="edit(item)">编辑</button><button v-if="canManage && item.status === 'active'" type="button" class="secondary-command" :disabled="busy" @click="disable(item)">停用</button></div>
    </article>
    <form v-if="editing" class="monitoring-form" @submit.prevent="save">
      <h4>{{ current ? '编辑监控服务（保存为新版本）' : '新增整机监控' }}</h4>
      <label>服务名称<input v-model="form.name" data-testid="monitoring-name" required maxlength="120" /></label>
      <label>业务用途<input v-model="form.description" /></label>
      <label>Prometheus 只读地址<input v-model="form.source_url" data-testid="monitoring-url" type="url" required placeholder="https://monitoring.example.com" /></label>
      <label>主机实例（instance 标签，精确匹配）<input v-model="form.instance" data-testid="monitoring-instance" required placeholder="host.example.com:9100" /></label>
      <label>只读令牌<input v-model="form.token" data-testid="monitoring-token" type="password" autocomplete="new-password" :placeholder="current?.has_token ? '已配置；留空保留现有凭据' : '数据源无需认证时可留空'" /></label>
      <label>采样间隔（秒）<input v-model.number="form.step_seconds" type="number" min="5" max="300" required /></label>
      <fieldset class="monitoring-metrics"><legend>采集指标</legend><label class="monitoring-check"><input v-model="form.cpu" type="checkbox" /><span>整机 CPU 使用率</span></label><label class="monitoring-check"><input v-model="form.memory" type="checkbox" /><span>整机内存使用率</span></label></fieldset>
      <p>后台从此地址只读采集；采样间隔不会提高数据源本身的精度。保存后请测试连接并确认有新鲜样本。</p>
      <label v-if="canAuthorize" class="monitoring-check monitoring-authorization"><input v-model="form.authorize_host" type="checkbox" /><span>明确授权平台访问此监控地址</span></label><p v-else>新地址需管理员授权；已授权地址可复用。</p>
      <div class="monitoring-actions"><button type="button" class="secondary-command" :disabled="busy" @click="editing = false; form.token = ''">取消</button><button type="submit" class="primary-command" data-testid="monitoring-save" :disabled="!valid || busy">保存到环境供后续复用</button></div>
    </form>
  </section>
</template>
<style scoped>
.monitoring-panel{display:grid;gap:14px;min-width:0}.monitoring-panel header,.monitoring-row{display:flex;justify-content:space-between;align-items:center;gap:16px}.monitoring-panel h3,.monitoring-panel h4,.monitoring-panel p{margin:0}.monitoring-panel p,.monitoring-panel small{font-size:13px;color:var(--text-secondary,#64748b);line-height:1.6}.monitoring-row{padding:12px 0;border-bottom:1px solid var(--border-color,#e2e8f0)}.monitoring-row small{display:block;overflow-wrap:anywhere}.monitoring-actions{display:flex;align-items:center;gap:8px;flex-wrap:wrap}.monitoring-actions button{display:inline-flex;white-space:nowrap;flex-shrink:0}.monitoring-form{display:grid;gap:12px;padding:16px;border:1px solid var(--border-color,#e2e8f0);border-radius:8px}.monitoring-form label{display:grid;gap:6px}.monitoring-form label:has(input[type=checkbox]){display:flex;align-items:center}.monitoring-form input:not([type=checkbox]){width:100%;min-width:0;padding:8px;border:1px solid #cbd5e1;border-radius:6px}.monitoring-panel [role=alert]{color:#b91c1c}@media(max-width:700px){.monitoring-panel header,.monitoring-row{align-items:flex-start;flex-direction:column}}

.monitoring-form{align-content:start;grid-template-columns:minmax(0,1fr);max-width:960px;width:100%;box-sizing:border-box}
.monitoring-form label{min-width:0;font-size:13px;line-height:1.5}
.monitoring-form input:not([type=checkbox]){box-sizing:border-box;min-height:36px;font-size:14px}
.monitoring-metrics{display:flex;flex-wrap:wrap;gap:12px 24px;margin:0;padding:12px;border:1px solid #e2e8f0;border-radius:6px;min-width:0}
.monitoring-metrics legend{padding:0 4px;font-size:13px;font-weight:600}
.monitoring-form .monitoring-check{display:flex;align-items:center;justify-content:flex-start;gap:8px;min-height:32px}
.monitoring-check span{min-width:0;word-break:normal;overflow-wrap:break-word}
.monitoring-form input[type=checkbox]{width:18px;height:18px;min-width:18px;min-height:18px;max-width:18px;max-height:18px;flex:0 0 18px;margin:0;padding:0;accent-color:#176da5}
.monitoring-authorization{padding:10px 12px;background:#f4f8fc;border-radius:6px}
.monitoring-form .monitoring-actions{justify-content:flex-end}
@media(min-width:900px){.monitoring-form{grid-template-columns:repeat(2,minmax(0,1fr))}.monitoring-form>h4,.monitoring-form>fieldset,.monitoring-form>p,.monitoring-form>.monitoring-authorization,.monitoring-form>.monitoring-actions{grid-column:1/-1}}
</style>
