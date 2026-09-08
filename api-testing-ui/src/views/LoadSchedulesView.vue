<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { apiClient } from '../api/client'
import { apiTestingHasPermission } from '../utils/authRedirect'

type Run = { id: string; project_id?: string; configuration: { scenario?: { name?: string }; environment?: { name?: string }; preflight?: { passed?: boolean }; workload?: Record<string, unknown> } }
type Schedule = { id: string; project_id?: string; archived?: boolean; active_run_id?: string; name: string; source_run_id: string; source_labels?: { scenario?: string; environment?: string; scenario_version?: number }; enabled: boolean; daily_time: string; next_run_at: string; notification_enabled: boolean; notification_status?: string; last_status: string; last_message: string; last_run_id?: string; snapshot: Record<string, unknown> }
const base = '/api/api-testing/v1/load-schedules'
const schedules = ref<Schedule[]>([]), runs = ref<Run[]>([])
const projects = ref<{ id: string; name: string }[]>([]), project = ref(sessionStorage.getItem('load-schedule-project') || ''), showArchived = ref(false)
const editing = ref<Schedule | null>(null), editName = ref(''), editTime = ref('09:00'), archiving = ref<Schedule | null>(null)
let timer: ReturnType<typeof setInterval> | null = null
let refreshing = false
const visible = computed(() => schedules.value.filter(row => (!project.value || row.project_id === project.value) && (showArchived.value || !row.archived)))
watch(project, value => { sessionStorage.setItem('load-schedule-project', value); source.value = '' })
const name = ref(''), source = ref(''), clock = ref('09:00'), notify = ref(false)
const busy = ref(false), error = ref(''), success = ref(''), confirmation = ref<Schedule | null>(null), confirmed = ref(false)
const canExecute = computed(() => apiTestingHasPermission('api.loadtest.execute'))
const canNotify = computed(() => apiTestingHasPermission('platform.notify'))
const eligible = computed(() => runs.value.filter(run => run.configuration?.preflight?.passed === true && (!project.value || run.project_id === project.value)))
const selected = computed(() => eligible.value.find(run => run.id === source.value))
const states: Record<string, string> = { idle: '尚未执行', claimed: '已认领', creating: '检查配置', connectivity: '连通性检查', preflighting: '功能预检', running: '执行中', finished: '已结束', failed: '执行失败', cancelled: '已取消', blocked: '本次已阻断', skipped_expired: '已跳过过期窗口' }
const notifications: Record<string, string> = { disabled: '未选择通知', pending: '等待报告', sending: '已提交发送，送达待确认', sent: '已发送', failed: '发送失败（不影响执行结论）' }
async function refresh(silent = false) {
  if (refreshing || (silent && busy.value)) return
  refreshing = true
  if (!silent) { error.value = ''; busy.value = true }
  try {
    const [plans, history] = await Promise.all([apiClient.get<{ schedules: Schedule[] }>(base), apiClient.get<{ runs: Run[] }>('/api/api-testing/v1/load-runs?limit=200')])
    schedules.value = plans.data.schedules; runs.value = history.data.runs
  } catch (e) { error.value = e instanceof Error ? e.message : '读取计划失败' } finally { if (!silent) busy.value = false; refreshing = false }
}
async function create() {
  if (!source.value || !name.value.trim() || busy.value) return
  error.value = ''; success.value = ''; busy.value = true
  try {
    await apiClient.post(base, { name: name.value.trim(), source_run_id: source.value, daily_time: clock.value, notification_enabled: notify.value })
    name.value = ''; notify.value = false
    await refresh(); success.value = '计划已保存并保持停用。核对固定配置后可单独启用。'
  } catch (e) { error.value = e instanceof Error ? e.message : '保存失败' } finally { busy.value = false }
}
async function toggle(row: Schedule, enabled: boolean) {
  busy.value = true; error.value = ''; success.value = ''
  try {
    await apiClient.put(`${base}/${encodeURIComponent(row.id)}`, { enabled, confirmed: enabled && confirmed.value })
    confirmation.value = null; confirmed.value = false
    await refresh(); success.value = enabled ? '计划已启用，将从下一个执行时间开始。' : '计划已停用；已开始的压力请在执行页停止。'
  } catch (e) { error.value = e instanceof Error ? e.message : '修改失败' } finally { busy.value = false }
}
function editable(row: Schedule) { return !row.archived && !row.enabled && !row.active_run_id && !['claimed','creating','connectivity','preflighting','running'].includes(row.last_status) }
async function saveMetadata(row: Schedule, payload: Record<string, unknown>) {
  busy.value = true; error.value = ''
  try { await apiClient.put(`${base}/${encodeURIComponent(row.id)}`, payload); editing.value = null; archiving.value = null; await refresh(); success.value = payload.archived ? '计划已归档，历史记录保留。' : '名称和执行时间已保存，固定压力配置保持原样。' }
  catch (e) { error.value = e instanceof Error ? e.message : '保存失败' } finally { busy.value = false }
}
function workload(snapshot: Record<string, unknown>) {
  const work = snapshot.workload as Record<string, unknown> | undefined
  if (!work) return '请查看固定配置'
  const model = { 'constant-vus': '固定并发', 'constant-arrival-rate': '固定吞吐', 'ramping-vus': '阶梯并发', 'ramping-arrival-rate': '阶梯吞吐' }[String(work.executor)] || '固定负载'
  return `${model} · ${work.vus != null ? `${work.vus} 个虚拟用户` : work.rate != null ? `${work.rate} 次/${work.time_unit === '1m' ? '分钟' : '秒'}` : '按保存的阶段执行'}${work.duration_seconds != null ? ` · ${work.duration_seconds} 秒` : ''}`
}
function date(value: string) { return new Date(value).toLocaleString('zh-CN', { timeZone: 'Asia/Shanghai', hour12: false }) }
onMounted(async () => {
  await refresh()
  try { const response = await apiClient.get<{ projects: { id: string; name: string }[] }>('/api/api-testing/v1/context-options'); projects.value = response.data.projects || [] } catch { /* Plans remain usable if the options request fails. */ }
})
onMounted(() => { timer = setInterval(() => { void refresh(true) }, 5000) })
onUnmounted(() => { if (timer) clearInterval(timer); timer = null })
</script>

<template>
  <main class="load-schedules">
    <header><div><h1>性能定时计划</h1><p>每日按已审查的只读配置运行，独立于功能回归基线。所有时间均为北京时间。</p></div><button :disabled="busy" @click="refresh()">刷新</button></header>
    <p v-if="error" role="alert" class="error">{{ error }}</p><p v-if="success" role="status" class="success">{{ success }}</p>
    <section class="panel filters"><label>应用筛选<select v-model="project" name="project-filter"><option value="">全部有权访问的应用</option><option v-for="item in projects" :key="item.id" :value="item.id">{{ item.name }}</option></select></label><label class="check"><input v-model="showArchived" type="checkbox"><span>显示已归档计划</span></label><p>状态每 5 秒自动刷新；筛选与正在填写的内容会保留。</p></section>
    <section v-if="canExecute" class="panel">
      <h2>从已预检的执行建立计划</h2><p>新计划默认停用。场景与环境版本、压力、节点、监控和停止条件保存后固定；更换配置请新建计划。</p>
      <form @submit.prevent="create">
        <label>计划名称<input v-model="name" name="name" required maxlength="160" placeholder="例如：每日只读搜索检查"></label>
        <label>已通过预检的执行<select v-model="source" name="source" required><option value="">请选择来源执行</option><option v-for="run in eligible" :key="run.id" :value="run.id">{{ run.configuration.scenario?.name || '性能执行' }} · {{ run.configuration.environment?.name || '固定环境' }} · {{ run.id.slice(0, 8) }}</option></select></label>
        <label>每日执行时间（北京时间）<input v-model="clock" type="time" required></label>
        <label class="check"><input v-model="notify" type="checkbox" :disabled="!canNotify"><span>执行结束后发送飞书报告（需已配置通知渠道及通知权限）</span></label>
        <p v-if="!eligible.length" class="hint">还没有可选择的预检记录。请先在压测执行页创建只读场景并完成连通性检查和功能预检。</p>
        <details v-if="selected"><summary>核对来源压力配置</summary><pre>{{ JSON.stringify(selected.configuration.workload, null, 2) }}</pre></details>
        <div class="actions"><RouterLink to="/load-runs">前往压测执行</RouterLink><button type="submit" :disabled="busy || !source || !name.trim()">保存为停用计划</button></div>
      </form>
    </section>
    <section class="plans"><h2>已有计划 · {{ visible.length }}</h2><p>错过 90 秒窗口自动跳过；同一计划不会重叠发压。权限失效、节点离线或预检失败时阻断本轮，不自动重试。</p><p v-if="!visible.length && !busy">暂无性能定时计划。</p>
      <div class="cards"><article v-for="row in visible" :key="row.id" class="panel"><header><h3>{{ row.name }}</h3><strong>{{ row.archived ? '已归档' : row.enabled ? '已启用' : '已停用' }}</strong></header><p>{{ row.source_labels?.scenario || '性能场景' }} · {{ row.source_labels?.environment || '固定环境' }}<span v-if="row.source_labels?.scenario_version"> · 场景 v{{ row.source_labels.scenario_version }}</span></p><p>{{ workload(row.snapshot) }}</p><p>每日 {{ row.daily_time }} · 北京时间</p><p>{{ row.enabled ? '下次执行' : '启用后重新计算下次时间' }}：{{ date(row.next_run_at) }}</p><p>{{ states[row.last_status] || '状态待确认' }} · {{ row.last_message }}</p><p>通知：{{ row.notification_enabled ? (notifications[row.notification_status || 'pending'] || '等待结果') : '未选择通知' }}</p><details><summary>查看固定配置</summary><pre>{{ JSON.stringify(row.snapshot, null, 2) }}</pre></details><div class="actions"><RouterLink v-if="row.last_run_id" :to="`/load-reports?run_id=${encodeURIComponent(row.last_run_id)}`">查看最近报告</RouterLink><button v-if="canExecute && row.enabled" :disabled="busy" @click="toggle(row, false)">停用计划</button><button v-else-if="canExecute && !row.archived" :disabled="busy" @click="confirmation = row; confirmed = false">核对并启用</button><button v-if="canExecute && editable(row)" :disabled="busy" @click="editing = row; editName = row.name; editTime = row.daily_time">调整名称与时间</button><button v-if="canExecute && editable(row)" :disabled="busy" @click="archiving = row">归档</button></div></article></div>
    </section>
    <section v-if="editing" class="confirmation panel" role="dialog" aria-modal="true" aria-label="调整计划名称与时间"><h2>调整名称与每日时间</h2><p>仅修改停用计划的名称和时间，固定环境与压力不变。</p><form @submit.prevent="saveMetadata(editing, { name: editName.trim(), daily_time: editTime })"><label>计划名称<input v-model="editName" name="edit-name" required maxlength="160"></label><label>每日北京时间<input v-model="editTime" name="edit-time" type="time" required></label><div class="actions"><button type="button" @click="editing = null">取消</button><button :disabled="busy || !editName.trim()" type="submit">保存调整</button></div></form></section>
    <section v-if="archiving" class="confirmation panel" role="dialog" aria-modal="true" aria-label="归档计划"><h2>归档“{{ archiving.name }}”</h2><p>归档后不再执行，固定配置和历史执行、通知记录仍保留。可通过“显示已归档计划”查看。</p><div class="actions"><button @click="archiving = null">取消</button><button :disabled="busy" @click="saveMetadata(archiving, { archived: true })">确认归档</button></div></section>
    <section v-if="confirmation" class="confirmation panel" role="dialog" aria-modal="true" aria-labelledby="confirm-title"><h2 id="confirm-title">启用“{{ confirmation.name }}”</h2><p>此操作会从下一个北京时间 {{ confirmation.daily_time }} 开始每日真实发压。请核对目标环境、节点和负载范围。</p><p><strong>{{ confirmation.source_labels?.scenario }} · {{ confirmation.source_labels?.environment }}</strong></p><p>{{ workload(confirmation.snapshot) }}</p><details><summary>展开固定配置明细</summary><pre>{{ JSON.stringify(confirmation.snapshot, null, 2) }}</pre></details><label class="check"><input v-model="confirmed" type="checkbox"><span>我已确认固定环境与压力，授权按此计划自动运行。</span></label><div class="actions"><button :disabled="busy" @click="confirmation = null">取消</button><button :disabled="busy || !confirmed" @click="toggle(confirmation, true)">确认启用</button></div></section>
  </main>
</template>
<style scoped>
.load-schedules{display:grid;gap:22px;min-width:0;padding:24px;color:#183047}.load-schedules header{display:flex;justify-content:space-between;align-items:center;gap:16px}.load-schedules h1{font-size:26px;margin:0 0 8px}.load-schedules h2{font-size:19px}.load-schedules h3{margin:0;font-size:17px}.load-schedules p{line-height:1.7;color:#53667c}.panel{min-height:0;background:#fff;border:1px solid #dbe3ec;border-radius:14px;padding:22px;min-width:0}.filters{display:flex;align-items:center;flex-wrap:wrap;gap:14px}.filters>label:first-child{min-width:240px}.filters p{margin:0;font-size:13px}.panel form{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:18px}.panel label{display:grid;gap:8px;min-width:0}.panel input,.panel select{box-sizing:border-box;min-width:0;width:100%;padding:10px;border:1px solid #bac9d9;border-radius:7px;font:inherit}.panel .check{display:flex;align-items:center;gap:10px}.panel .check input[type=checkbox]{width:18px;height:18px;flex:0 0 18px;padding:0;margin:0}.actions{display:flex;flex-wrap:wrap;gap:12px;justify-content:flex-end;align-items:center}.panel form .actions,.panel form .hint,.panel form details{grid-column:1/-1}.load-schedules button{width:auto;display:inline-flex;align-items:center;justify-content:center;white-space:nowrap;padding:9px 15px;border:1px solid #bbcede;border-radius:8px;background:#eef5fc;color:#174367;cursor:pointer}.load-schedules button:disabled{opacity:.5;cursor:not-allowed}.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,380px),480px));gap:18px}.cards .actions{margin-top:18px}.load-schedules pre{white-space:pre-wrap;overflow-wrap:anywhere;max-height:280px;overflow:auto;background:#f2f6fa;padding:12px;border-radius:8px}.error{background:#fff0ed;padding:12px}.success{background:#eaf8ef;padding:12px}.confirmation{position:fixed;top:50%;left:50%;transform:translate(-50%,-50%);width:min(560px,calc(100vw - 32px));box-sizing:border-box;height:auto;max-height:80vh;overflow:auto;z-index:50;box-shadow:0 0 0 100vmax #10283d66}.confirmation .actions{margin-top:20px}@media(max-width:700px){.load-schedules{padding:14px}.panel{padding:16px}.filters{display:flex;align-items:center;flex-wrap:wrap;gap:14px}.filters>label:first-child{min-width:240px}.filters p{margin:0;font-size:13px}.panel form{grid-template-columns:1fr}.load-schedules header{align-items:flex-start}.confirmation{width:calc(100vw - 28px);max-height:90vh}}
</style>
