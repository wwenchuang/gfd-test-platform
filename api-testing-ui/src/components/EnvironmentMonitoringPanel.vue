<script setup lang="ts">
import { computed, reactive, ref, watch } from 'vue'
import { monitoringApi, type MonitoringDeployment, type MonitoringService } from '../api/monitoring'
import { monitoringMetricOptions, monitoringMetricLabel, monitoringScopeLabel } from '../utils/monitoringMetrics'
import { apiTestingHasPermission } from '../utils/authRedirect'
const props = withDefaults(defineProps<{ environmentRevisionId: string; readonly?: boolean }>(), { readonly: false })
const emit = defineEmits<{ changed: []; saved: [service: MonitoringService] }>()
const items = ref<MonitoringService[]>([])
const loading = ref(false)
const busy = ref(false)
const error = ref('')
const editing = ref(false)
const current = ref<MonitoringService>()
const form = reactive({ name: '', description: '', source_url: '', instance: '', token: '', step_seconds: 15, authorize_host: false, deployment: 'host' as MonitoringDeployment, id: '', namespace: '', pod: '', datname: '', metrics: ['cpu_percent', 'memory_percent'] })
const canCheck = computed(() => apiTestingHasPermission('api.environment') || apiTestingHasPermission('api.loadtest.execute'))
const canManage = computed(() => !props.readonly && apiTestingHasPermission('api.environment'))
const canAuthorize = computed(() => apiTestingHasPermission('platform.configure'))
const valid = computed(() => form.name.trim() && /^https?:\/\//.test(form.source_url) && form.instance.trim() && Number.isInteger(form.step_seconds) && form.step_seconds >= 5 && form.step_seconds <= 300 && form.metrics.length > 0 && (form.deployment !== 'container' || (form.id.startsWith('/') && form.id !== '/')) && (form.deployment !== 'pod' || (form.namespace.trim() && form.pod.trim())) && (form.deployment !== 'postgres' || form.datname.trim()))
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
  Object.assign(form, { name: item?.name || '', description: item?.description || '', source_url: item?.source_url || '', instance: item?.labels.instance || '', token: '', step_seconds: item?.step_seconds || 15, authorize_host: false, deployment: item?.deployment || 'host', id: item?.labels.id || '', namespace: item?.labels.namespace || '', pod: item?.labels.pod || '', datname: item?.labels.datname || '', metrics: item ? [...item.metrics] : ['cpu_percent', 'memory_percent'] })
  editing.value = true; error.value = ''
}
async function save(): Promise<void> {
  if (!canManage.value || !valid.value || busy.value) return
  busy.value = true; error.value = ''
  const scope = props.environmentRevisionId
  try {
    const service = await monitoringApi.save(current.value?.id, { environment_revision_id: scope, name: form.name.trim(), description: form.description.trim(), deployment: form.deployment, source_url: form.source_url.trim(), labels: { ...(current.value?.deployment === form.deployment || (!current.value?.deployment && form.deployment === 'host') ? current.value?.labels : {}), instance: form.instance.trim(), ...(form.deployment === 'container' ? { id: form.id.trim() } : {}), ...(form.deployment === 'pod' ? { namespace: form.namespace.trim(), pod: form.pod.trim() } : {}), ...(form.deployment === 'postgres' ? { datname: form.datname.trim() } : {}) }, metrics: [...form.metrics], step_seconds: form.step_seconds, ...(form.token ? { token: form.token } : {}), ...(canAuthorize.value && form.authorize_host ? { authorize_host: true } : {}) })
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
    <header><div><h3>服务与监控</h3><p>支持整机 CPU、内存、网络与磁盘，以及 cAdvisor 容器 / 指定 Pod 内各容器和 PostgreSQL 指定数据库指标。当前不采集单个服务进程或其他数据库类型。</p></div><button v-if="canManage" type="button" class="secondary-command" data-testid="monitoring-create" :disabled="busy || loading" @click="edit()">新增监控服务</button></header>
    <p v-if="error" role="alert">{{ error }}</p>
    <p v-if="loading" role="status">正在读取监控配置…</p>
    <p v-else-if="!items.length">当前环境暂无监控服务。</p>
    <article v-for="item in items" :key="item.id" class="monitoring-row">
      <div><strong>{{ item.name }}</strong><small>{{ Object.values(item.labels).join(' / ') }} · {{ monitoringScopeLabel(item.deployment) }} · {{ item.status === 'active' ? '启用' : '已停用' }} · {{ item.metrics.map(monitoringMetricLabel).join('、') }}</small><small>{{ item.last_check?.message || '尚未检查采集' }}</small><small v-if="item.last_check?.checked_at">最近检查：{{ new Date(item.last_check.checked_at).toLocaleString() }}</small></div>
      <div class="monitoring-actions"><button v-if="canCheck" type="button" class="secondary-command" :disabled="busy || item.status !== 'active'" @click="check(item)">测试连接</button><button v-if="canManage" type="button" class="secondary-command" :disabled="busy" @click="edit(item)">编辑</button><button v-if="canManage && item.status === 'active'" type="button" class="secondary-command" :disabled="busy" @click="disable(item)">停用</button></div>
    </article>
    <form v-if="editing" class="monitoring-form" @submit.prevent="save">
      <h4>{{ current ? '编辑监控服务（保存为新版本）' : '新增资源监控' }}</h4>
      <label>服务名称<input v-model="form.name" data-testid="monitoring-name" required maxlength="120" /></label>
      <label>资源范围<select v-model="form.deployment" data-testid="monitoring-deployment" @change="form.metrics = monitoringMetricOptions[form.deployment].slice(0, 2).map(item => item.key)"><option value="host">整机（node_exporter）</option><option value="container">指定容器（cAdvisor）</option><option value="pod">指定 Pod 内各容器（cAdvisor）</option><option value="postgres">PostgreSQL 指定数据库（postgres_exporter）</option></select></label>
      <label v-if="form.deployment === 'container'">容器 cgroup id（精确，不能为 /）<input v-model="form.id" data-testid="monitoring-container-id" required placeholder="/docker/实际容器ID" /></label>
      <label v-if="form.deployment === 'pod'">命名空间（namespace）<input v-model="form.namespace" data-testid="monitoring-namespace" required /></label>
      <label v-if="form.deployment === 'pod'">Pod 名称（精确匹配）<input v-model="form.pod" data-testid="monitoring-pod" required /></label>
      <label v-if="form.deployment === 'postgres'">数据库名称（datname，精确匹配）<input v-model="form.datname" data-testid="monitoring-datname" required /></label>
      <label>业务用途<input v-model="form.description" /></label>
      <label>Prometheus 只读地址<input v-model="form.source_url" data-testid="monitoring-url" type="url" required placeholder="https://monitoring.example.com" /></label>
      <label>采集实例（instance 标签，精确匹配）<input v-model="form.instance" data-testid="monitoring-instance" required placeholder="host.example.com:9100" /></label>
      <label>只读令牌<input v-model="form.token" data-testid="monitoring-token" type="password" autocomplete="new-password" :placeholder="current?.has_token ? '已配置；留空保留现有凭据' : '数据源无需认证时可留空'" /></label>
      <label>采样间隔（秒）<input v-model.number="form.step_seconds" type="number" min="5" max="300" required /></label>
      <fieldset class="monitoring-metrics"><legend>采集指标</legend><label v-for="metric in monitoringMetricOptions[form.deployment]" :key="metric.key" class="monitoring-check"><input v-model="form.metrics" :value="metric.key" type="checkbox" /><span>{{ metric.label }}</span></label></fieldset>
      <p v-if="form.deployment === 'container' || form.deployment === 'pod'">需已接入 cAdvisor 指标。Pod 按真实容器分列，排除基础设施容器；无 CPU 配额时占比未知。未接入工作负载聚合、重启、容器网络和磁盘。</p>
      <p v-else-if="form.deployment === 'postgres'">需已接入 postgres_exporter 的 pg_stat_database 指标。连接数包含空闲连接；数据库提交/回滚不等于业务链路成功/失败。未接入最大连接额度、慢查询和锁等待，不支持其他数据库类型。</p>
      <p v-else>网络、磁盘逐设备展示，不将虚拟网卡、磁盘、分区和重复挂载相加。空间按 size-free 统计；平均 I/O 延迟在零操作时未知，建议按需选为可选监控。没有监控源时，请由管理员按仓库 deploy/load-monitoring/README.md 部署标准主机采集包。</p>
      <p>后台从此地址只读采集；采样间隔不会提高数据源本身的精度。保存后请测试连接并确认有新鲜样本。</p>
      <label v-if="canAuthorize" class="monitoring-check monitoring-authorization"><input v-model="form.authorize_host" type="checkbox" /><span>明确授权平台访问此监控地址</span></label><p v-else>新地址需管理员授权；已授权地址可复用。</p>
      <div class="monitoring-actions"><button type="button" class="secondary-command" :disabled="busy" @click="editing = false; form.token = ''">取消</button><button type="submit" class="primary-command" data-testid="monitoring-save" :disabled="!valid || busy">保存到环境供后续复用</button></div>
    </form>
  </section>
</template>
<style scoped>
.monitoring-panel{display:grid;gap:14px;min-width:0}.monitoring-panel header,.monitoring-row{display:flex;justify-content:space-between;align-items:center;gap:16px}.monitoring-panel h3,.monitoring-panel h4,.monitoring-panel p{margin:0}.monitoring-panel p,.monitoring-panel small{font-size:13px;color:var(--text-secondary,#64748b);line-height:1.6}.monitoring-row{padding:12px 0;border-bottom:1px solid var(--border-color,#e2e8f0)}.monitoring-row small{display:block;overflow-wrap:anywhere}.monitoring-actions{display:flex;align-items:center;gap:8px;flex-wrap:wrap}.monitoring-actions button{display:inline-flex;white-space:nowrap;flex-shrink:0}.monitoring-form{display:grid;gap:12px;padding:16px;border:1px solid var(--border-color,#e2e8f0);border-radius:8px}.monitoring-form label{display:grid;gap:6px}.monitoring-form label:has(input[type=checkbox]){display:flex;align-items:center}.monitoring-form input:not([type=checkbox]){width:100%;min-width:0;padding:8px;border:1px solid #cbd5e1;border-radius:6px}.monitoring-panel [role=alert]{color:#b91c1c}@media(max-width:700px){.monitoring-panel header,.monitoring-row{align-items:flex-start;flex-direction:column}}

.monitoring-form{align-content:start;grid-template-columns:minmax(0,1fr);max-width:960px;width:100%;box-sizing:border-box}
.monitoring-panel>header>div{min-width:0}
.monitoring-panel>header>button{flex:0 0 auto;white-space:nowrap;display:inline-flex;align-items:center}
.monitoring-form label{min-width:0;font-size:13px;line-height:1.5}
.monitoring-form select{min-height:36px;width:100%;border:1px solid #cbd5e1;border-radius:6px;background:white;padding:8px}.monitoring-form input:not([type=checkbox]){box-sizing:border-box;min-height:36px;font-size:14px}
.monitoring-metrics{display:flex;flex-wrap:wrap;gap:12px 24px;margin:0;padding:12px;border:1px solid #e2e8f0;border-radius:6px;min-width:0}
.monitoring-metrics legend{padding:0 4px;font-size:13px;font-weight:600}
.monitoring-form .monitoring-check{display:flex;align-items:center;justify-content:flex-start;gap:8px;min-height:32px}
.monitoring-check span{min-width:0;word-break:normal;overflow-wrap:break-word}
.monitoring-form input[type=checkbox]{width:18px;height:18px;min-width:18px;min-height:18px;max-width:18px;max-height:18px;flex:0 0 18px;margin:0;padding:0;accent-color:#176da5}
.monitoring-authorization{padding:10px 12px;background:#f4f8fc;border-radius:6px}
.monitoring-form .monitoring-actions{justify-content:flex-end}
@media(min-width:900px){.monitoring-form{grid-template-columns:repeat(2,minmax(0,1fr))}.monitoring-form>h4,.monitoring-form>fieldset,.monitoring-form>p,.monitoring-form>.monitoring-authorization,.monitoring-form>.monitoring-actions{grid-column:1/-1}}
</style>
