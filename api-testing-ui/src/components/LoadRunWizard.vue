<script setup lang="ts">
import type { NextRunPreset } from '../utils/loadNextRun'
import { computed, ref } from 'vue'
import LoadThresholdEditor, {type ErrorCriteria} from './LoadThresholdEditor.vue'
import LoadTestIntent, { type TestContext, type StopPolicy } from './LoadTestIntent.vue'
import type { EnvironmentRevisionOption, LoadAgent, LoadScenario } from '../api/contracts'
import LoadMonitoringSelector from './LoadMonitoringSelector.vue'
import type { MonitoringSelection } from '../api/monitoring'
import { apiTestingHasPermission } from '../utils/authRedirect'

type Executor = 'constant-vus' | 'ramping-vus' | 'constant-arrival-rate' | 'ramping-arrival-rate'

const props = defineProps<{ scenario: LoadScenario; environments: EnvironmentRevisionOption[]; agents: LoadAgent[]; projectName?: string; initialEnvironmentId?: string; preset?: NextRunPreset }>()
const emit = defineEmits<{ submit: [payload: Record<string, unknown>]; cancel: [] }>()
const environmentId = ref(props.preset?.environmentId || (props.environments.some(item => item.id === props.initialEnvironmentId) ? props.initialEnvironmentId! : props.environments[0]?.id || ''))
const executor = ref<Executor>(props.preset?.executor || 'constant-arrival-rate')
const vus = ref(props.preset && !props.preset.executor.includes('arrival-rate') ? props.preset.target : 1)
const maxVus = ref(props.preset?.maxVus || 1)
const rate = ref(props.preset?.target || 1)
const timeUnit = props.preset?.timeUnit || '1s'
const rateDivisor = timeUnit === '1m' ? 60 : 1
const duration = ref(props.preset?.duration || 10)
const p95 = ref(500)
const selectedIds = ref<string[]>([])
const allSelected = ref(false)
const distributionValid = computed(() => !allSelected.value || executor.value !== 'constant-arrival-rate' || (rate.value >= selected.value.length && requestedMaxVus.value >= selected.value.length && capacityEnough.value))
const allowFallback = ref(false)
const allowRunAnyway = ref(false)
const priority = ref('normal')
const productionConfirmed = ref(false)
const testContext = ref<TestContext>(props.preset?.testContext || {purpose: 'smoke', release: '', data_profile: '', cache_state: '', notes: ''})
const stopPolicy = ref<StopPolicy>(props.preset?.stopPolicy || null)
const errorCriteria = ref<ErrorCriteria>({httpPercent:1, workflowPercent:0, businessEnabled:false, businessPercent:0})
const criteriaValid = computed(() => [errorCriteria.value.httpPercent,errorCriteria.value.workflowPercent,...(errorCriteria.value.businessEnabled?[errorCriteria.value.businessPercent]:[])].every(value=>Number.isFinite(value)&&value>=0&&value<=100))
const stages = ref<Array<{duration_seconds: number; target: number}>>([])
const ramping = computed(() => executor.value.startsWith('ramping'))
const activeStages = computed(() => stages.value.length ? stages.value : [{duration_seconds: duration.value, target: arrivalModel.value ? rate.value : vus.value}])
const stagePeak = computed(() => Math.max(1, ...activeStages.value.map(item => item.target)))
const totalDuration = computed(() => ramping.value ? activeStages.value.reduce((n, item) => n + item.duration_seconds, 0) : duration.value)
const numericValid = computed(() => [vus.value, maxVus.value, rate.value, duration.value, p95.value].every(v => Number.isInteger(v) && v > 0) && (!ramping.value || activeStages.value.every(s => Number.isInteger(s.target) && s.target >= 0 && Number.isInteger(s.duration_seconds) && s.duration_seconds > 0)) && (!stopPolicy.value || (stopPolicy.value.http_error_rate > 0 && stopPolicy.value.http_error_rate <= 1 && Number.isInteger(stopPolicy.value.grace_seconds) && stopPolicy.value.grace_seconds >= 10 && stopPolicy.value.grace_seconds <= 600)))
function stageTemplate() { const target = arrivalModel.value ? rate.value : vus.value; stages.value = [{duration_seconds: 60, target}, {duration_seconds: duration.value, target}, {duration_seconds: 60, target: 0}] }
const monitoring = ref<MonitoringSelection>({ services: [], before_seconds: 60, after_seconds: 60 })
const monitoringValid = computed(() => !monitoring.value.services.length || [monitoring.value.before_seconds, monitoring.value.after_seconds].every(value => Number.isInteger(value) && value >= 0 && value <= 600))

function agentSelectable(item: LoadAgent): boolean { return item.status === 'online' && item.calibration_state === 'valid' && item.health.schedulable !== false && item.scheduling_tier !== 'disabled' && (allowFallback.value || item.scheduling_tier !== 'fallback') }
const validAgents = computed(() => props.agents.filter(agentSelectable))
const unavailableHint = computed(() => props.agents.some(a => a.status === 'online' && a.calibration_state === 'valid' && (a.current_usage.processes || 0) > 0)
  ? '已校准节点正在执行其他任务，当前没有空闲执行进程。请等待任务结束后刷新节点状态，无需重复校准。'
  : '当前没有符合调度条件的节点。请在压测节点页检查在线状态、校准有效期及调度级别。')
const selected = computed(() => validAgents.value.filter(item => selectedIds.value.includes(item.id)))
const invalidSelection = computed(() => selected.value.length !== selectedIds.value.length)
const environment = computed(() => props.environments.find(item => item.id === environmentId.value))
const production = computed(() => /生产|正式|prod(uction)?/i.test(environment.value?.name || ''))
const hasProductionPermission = computed(() => apiTestingHasPermission('api.production'))
const arrivalModel = computed(() => executor.value.includes('arrival-rate'))
const targetIterations = computed(() => { if (!ramping.value) return rate.value * duration.value / rateDivisor; let previous = 1; return Math.round(activeStages.value.reduce((total, s) => { const amount = (previous + s.target) / 2 * s.duration_seconds; previous = s.target; return total + amount }, 0) / rateDivisor) })
const iterationEstimate = computed(() => arrivalModel.value
  ? `按目标吞吐预计约 ${targetIterations.value} 次完整链路。`
  : `${executor.value === 'constant-vus' ? '固定并发' : '阶梯并发'}会在时长内持续循环；实际次数取决于接口响应时间，不能按 VU × 秒数推算。`)

function availableCapacity(agent: LoadAgent, field: 'max_vus' | 'max_iterations_per_second'): number {
  const candidates = [agent.hard_limits[field], agent.soft_limits[field], agent.health.calibration?.[field]]
    .map(value => Number(value || 0))
  const limit = candidates.length ? Math.min(...candidates) : 0
  const usageKey = field === 'max_vus' ? 'vus' : 'iterations_per_second'
  return Math.max(0, Math.floor(limit - Number(agent.current_usage[usageKey] || 0)))
}

const selectedCapacity = computed(() => selected.value.reduce((total, agent) => ({
  vus: total.vus + availableCapacity(agent, 'max_vus'),
  rate: total.rate + availableCapacity(agent, 'max_iterations_per_second'),
}), { vus: 0, rate: 0 }))
const requestedMaxVus = computed(() => arrivalModel.value ? Math.max(vus.value, maxVus.value) : ramping.value ? stagePeak.value : vus.value)
const capacityEnough = computed(() => arrivalModel.value
  ? selectedCapacity.value.rate >= (ramping.value ? stagePeak.value : rate.value) / rateDivisor && selectedCapacity.value.vus >= requestedMaxVus.value
  : selectedCapacity.value.vus >= requestedMaxVus.value)
const productionReady = computed(() => !production.value || (hasProductionPermission.value && productionConfirmed.value))
const canSubmit = computed(() => Boolean(
  environmentId.value && props.scenario.active_version_id && selected.value.length
  && (!props.preset || !ramping.value || stages.value.length > 0) && distributionValid.value && !invalidSelection.value && numericValid.value && criteriaValid.value && monitoringValid.value && productionReady.value && (capacityEnough.value || allowRunAnyway.value),
))

function thresholdText(key: string, raw: unknown): string {
  const item = raw as Record<string, unknown>
  const label = ({p95_ms:'P95 响应时间',p99_ms:'P99 响应时间',http_error_rate:'HTTP 错误率',workflow_failure_rate:'完整链路失败率',business_failure_rate:'业务失败率'} as Record<string,string>)[key] || key
  const operator = ({less_than:'小于',less_than_or_equal:'不超过',greater_than:'大于',greater_than_or_equal:'不低于',equals:'等于'} as Record<string,string>)[String(item.operator)] || String(item.operator)
  const value = key.endsWith('_rate') ? `${Number(item.value)*100}%` : `${item.value}${key.endsWith('_ms') ? ' 毫秒' : ''}`
  return `${label} ${operator} ${value} · ${item.required ? '必须达标' : '参考项'}`
}
function selectExecutor(value: string): void { executor.value = value as Executor }
function toggleAgent(id: string, checked: boolean): void {
  selectedIds.value = checked ? [...new Set([...selectedIds.value, id])] : selectedIds.value.filter(item => item !== id)
}
function recommendAgents(): void {
  const order = { preferred: 0, normal: 1, fallback: 2, disabled: 3 }
  const candidates = validAgents.value.slice().sort((a, b) => order[a.scheduling_tier] - order[b.scheduling_tier])
  const next: string[] = []
  let selectedVus = 0
  let selectedRate = 0
  for (const agent of candidates) {
    next.push(agent.id)
    selectedVus += availableCapacity(agent, 'max_vus')
    selectedRate += availableCapacity(agent, 'max_iterations_per_second')
    if (selectedVus >= requestedMaxVus.value && (!arrivalModel.value || selectedRate >= (ramping.value ? stagePeak.value : rate.value) / rateDivisor)) break
  }
  selectedIds.value = next
}
function workload(): Record<string, unknown> {
  if (executor.value === 'constant-vus') return { executor: executor.value, vus: vus.value, duration_seconds: duration.value }
  if (executor.value === 'ramping-vus') return { executor: executor.value, start_vus: 1, stages: activeStages.value.map(s => ({...s})) }
  if (executor.value === 'constant-arrival-rate') return {
    executor: executor.value, rate: rate.value, time_unit: timeUnit, duration_seconds: duration.value,
    pre_allocated_vus: Math.max(1, vus.value), max_vus: requestedMaxVus.value,
  }
  return {
    executor: executor.value, start_rate: 1, time_unit: timeUnit,
    pre_allocated_vus: Math.max(1, vus.value), max_vus: requestedMaxVus.value,
    stages: activeStages.value.map(s => ({...s})),
  }
}
function submit(): void {
  if (!canSubmit.value) return
  emit('submit', {
    scenario_version_id: props.preset?.scenarioVersionId || props.scenario.active_version_id,
    environment_revision_id: environmentId.value,
    workload: workload(),
    thresholds: props.preset?.thresholds || { http_error_rate: {operator: 'less_than_or_equal', value:errorCriteria.value.httpPercent/100, required:true}, workflow_failure_rate:{operator:'less_than_or_equal',value:errorCriteria.value.workflowPercent/100,required:true}, ...(errorCriteria.value.businessEnabled?{business_failure_rate:{operator:'less_than_or_equal',value:errorCriteria.value.businessPercent/100,required:true}}:{}), p95_ms: { operator: 'less_than_or_equal', value: p95.value, required: true } },
    priority: priority.value,
    test_context: testContext.value,
    ...(stopPolicy.value ? {stop_policy: stopPolicy.value} : {}),
    ...(props.preset?.monitoring.services.length ? {monitoring: props.preset.monitoring} : monitoring.value.services.length ? { monitoring: monitoring.value } : {}),
    allocation_policy: {
      allow_fallback: allowFallback.value,
      allow_run_anyway: allowRunAnyway.value,
      agent_ids: selectedIds.value,
      distribution: allSelected.value && executor.value === 'constant-arrival-rate' ? 'all_selected' : 'priority',
    },
  })
}
</script>

<template>
  <section class="load-wizard" aria-label="创建压测执行">
    <header><div><p class="eyebrow">压测配置</p><h2>{{ scenario.name }}</h2></div><button data-testid="load-run-back" class="text-command" type="button" @click="emit('cancel')">← 返回执行列表</button></header>
    <div class="load-wizard-body">
      <LoadTestIntent v-model="testContext" v-model:stop-policy="stopPolicy" />
      <section v-if="preset" class="load-review-box" data-testid="load-next-review"><strong>下一轮验证 · 配置核对</strong><p v-if="preset.reason">建议依据：{{ preset.reason }}</p><p v-if="preset.objective">本轮要验证：{{ preset.objective }}</p><p>来源执行：{{ preset.sourceId }}</p><p>上一轮：{{ preset.previous }} → 建议：{{ preset.target }} {{ !preset.executor.includes('arrival-rate') ? 'VU' : timeUnit === '1m' ? '次/分钟' : '次/秒' }} · {{ preset.duration }} 秒</p><p>固定保留原场景版本和环境、全部验收阈值及 {{ preset.monitoring.services.length }} 项监控。压力可调整，节点请重新选择。创建后仍需连通性检查、预检和启动确认。</p><ul><li v-for="(value, key) in preset.thresholds" :key="key">{{ thresholdText(String(key), value) }}</li></ul></section>
      <LoadThresholdEditor v-else v-model="errorCriteria" />
      <section class="load-context-banner"><div><span>所属应用 / API 项目</span><strong>{{ projectName || '当前接口项目' }}</strong><small>场景、环境和报告都归入这个项目；需要换应用时请先回工作台切换。</small></div><div><span>场景版本</span><strong>{{ scenario.name }}</strong><small>版本：{{ preset?.scenarioVersionId || scenario.active_version_id }}；历史结果可重复核对。</small></div></section>
      <label>{{ preset ? '目标环境（沿用原版本）' : '目标环境（可切换）' }}<select v-model="environmentId" :disabled="Boolean(preset)" data-testid="load-run-environment"><option value="" disabled>请选择目标环境</option><option v-for="item in environments" :key="item.id" :value="item.id">{{ item.name }} · v{{ item.revision }}</option></select><small v-if="preset">本次沿用原环境；如需更换，请从新建压测入口配置。</small><small v-else-if="environments.length === 1">当前项目只有 1 个可用环境；可到“环境配置”新增独立压测环境。</small><small v-else>请选择本次真实接收流量的环境，环境不是写死的。</small></label>
      <p v-if="!environments.length" class="load-warning">当前项目没有可用环境，请先到“环境配置”创建并验证服务地址。</p>
      <p v-if="production && !hasProductionPermission" class="load-warning">当前账号没有 api.production 权限，不能创建生产环境压测。请联系管理员授权。</p>
      <p v-else-if="production" class="load-warning">生产环境会持续收到真实请求，请确认范围、数据和停止条件。</p>

      <h3>负载模型</h3>
      <div class="load-option-grid">
        <button v-for="item in [{ value: 'constant-vus', label: '固定并发', help: '保持固定虚拟用户，VU 不等于 QPS。' }, { value: 'ramping-vus', label: '阶梯并发', help: '逐步升高并发，观察性能拐点。' }, { value: 'constant-arrival-rate', label: '固定吞吐', help: '保持每秒完整链路次数，适合验证目标 QPS。' }, { value: 'ramping-arrival-rate', label: '阶梯吞吐', help: '逐级提高吞吐，寻找容量上限。' }]" :key="item.value" :data-testid="`load-model-${item.value}`" :class="{ active: executor === item.value }" type="button" @click="selectExecutor(item.value)"><strong>{{ item.label }}</strong><span>{{ item.help }}</span></button>
      </div>
      <p class="load-model-guide"><strong>第一次怎么选：</strong>只想确认流程时，使用安全的只读接口和“固定吞吐”，先从 1 次/秒、10 秒开始；“固定并发”用于模拟同时在线用户，不会把请求速度限制为 VU 数。</p>
      <div class="load-field-grid">
        <label v-if="!arrivalModel">并发用户（VU）<input v-model.number="vus" data-testid="load-vus" type="number" min="1" /></label>
        <template v-else><label>目标吞吐（{{ timeUnit === '1m' ? '次/分钟' : '次/秒' }}）<input v-model.number="rate" data-testid="load-rate" type="number" min="1" /></label><label>预分配并发（VU）<input v-model.number="vus" data-testid="load-vus" type="number" min="1" /></label><label>最大并发（VU）<input v-model.number="maxVus" data-testid="load-max-vus" type="number" min="1" /><small>响应变慢时，k6 最多扩到该并发维持目标吞吐；不得超过节点可用容量。</small></label></template>
        <label>持续时间（秒）<input v-model.number="duration" data-testid="load-duration" type="number" min="1" /></label>
        <label v-if="!preset">P95 响应上限（毫秒）<input v-model.number="p95" data-testid="load-p95" type="number" min="1" /></label>
        <label>排队优先级<select v-model="priority"><option value="urgent">紧急（优先排队，不抢占运行任务）</option><option value="high">高</option><option value="normal">普通（日常默认）</option><option value="low">低</option></select></label>
      </div>

      <section v-if="ramping" class="load-stage-editor" data-testid="load-stage-editor">
        <h3>明确每个压力阶段</h3>
        <p v-if="preset && !stages.length" class="load-warning">AI 只提供目标与总时长，未提供阶段明细。请添加并核对每段时长和目标后创建；平台不会自动编造阶段。</p>
        <p>各阶段逐步变化到填写的目标；总时长为阶段时长之和。吞吐单位：{{ timeUnit === '1m' ? '次/分钟' : '次/秒' }}，并发单位：VU。</p>
        <div v-for="(stage,index) in stages" :key="index" class="load-stage-row"><strong>阶段 {{ index+1 }}</strong><label>时长（秒）<input v-model.number="stage.duration_seconds" :data-testid="`load-stage-duration-${index}`" type="number" min="1" /></label><label>阶段目标<input v-model.number="stage.target" :data-testid="`load-stage-target-${index}`" type="number" min="0" /></label><button type="button" class="secondary-command" @click="stages.splice(index,1)">删除</button></div>
        <button type="button" data-testid="load-stage-add" class="secondary-command" @click="stages.push({duration_seconds: duration,target:arrivalModel?rate:vus})">＋ 添加阶段（请核对目标和时长）</button>
      </section>
      <div class="load-section-heading"><div><h3>按负载目标选择压测节点</h3><small>先填写上面的并发/吞吐，再让平台按“首选 → 普通 → 备用”推荐足够容量。</small></div><button data-testid="load-agent-recommend" class="secondary-command" type="button" :disabled="!validAgents.length" @click="recommendAgents">选择推荐节点</button></div>
      <p v-if="!validAgents.length" class="load-warning">{{ unavailableHint }}</p>
      <div class="load-agent-options"><label v-for="item in agents" :key="item.id" :class="{ selected: selectedIds.includes(item.id), disabled: !agentSelectable(item) }"><input :data-testid="`load-agent-${item.id}`" type="checkbox" :disabled="!agentSelectable(item)" :checked="selectedIds.includes(item.id)" @change="toggleAgent(item.id, ($event.target as HTMLInputElement).checked)" /><span><strong>{{ item.name }}</strong><small>{{ item.scheduling_tier === 'preferred' ? '首选节点' : item.scheduling_tier === 'normal' ? '普通节点' : item.scheduling_tier === 'disabled' ? '已停用' : '备用节点' }}</small><small v-if="item.calibration_state !== 'valid'">{{ item.calibration_state === 'expired' ? '校准过期，不能选择' : '未完成有效校准，不能选择' }}</small><small v-else>可分配 {{ availableCapacity(item, 'max_vus') }} VU / {{ availableCapacity(item, 'max_iterations_per_second') }} 次/秒</small></span></label></div>
      <p v-if="invalidSelection" class="load-warning">所选节点状态已变化，不能继续使用。<button type="button" class="secondary-command" @click="selectedIds = selected.map(item => item.id)">清除不可用选择</button>也可以重新选择推荐节点。</p>
      <p class="load-capacity-note"><strong>选择依据：</strong>任务容量按每台节点的“本机硬上限、平台容量策略、校准达到值”取最小值，再减当前占用；校准值不会直接全部分配。默认勾选的是候选节点池，平台优先使用首选节点；开启“所有选中节点共同参与”后会拆分总压力给每台节点。</p>
      <label v-if="executor === 'constant-arrival-rate'" class="load-check"><input v-model="allSelected" data-testid="load-distribution-all" type="checkbox" />所有选中节点共同参与（拆分总压力，不按节点数放大）</label>
      <p v-if="allSelected && executor === 'constant-arrival-rate'" class="load-capacity-note">每台至少分到 1 次目标吞吐和 1 VU。总压力或容量不足时禁止创建，请减少节点或调整总目标。</p>
      <label class="load-check"><input v-model="allowFallback" type="checkbox" />允许调度级别为“备用”的节点参与（与节点名称无关）</label>
      <p v-if="selected.length && !capacityEnough" class="load-warning" data-testid="capacity-shortfall">所选节点容量不足：{{ arrivalModel ? `最大并发需要 ${requestedMaxVus} VU、目标吞吐需要 ${(ramping ? stagePeak : rate) / rateDivisor} 次/秒；` : `并发需要 ${requestedMaxVus} VU；` }}合计可用 {{ selectedCapacity.vus }} VU / {{ selectedCapacity.rate }} 次/秒。请降低目标或增加节点。</p>
      <label class="load-check"><input v-model="allowRunAnyway" data-testid="allow-run-anyway" type="checkbox" />容量不足时仍创建任务（报告固定标记为证据不足）</label>
      <label v-if="production && hasProductionPermission" class="load-check"><input v-model="productionConfirmed" type="checkbox" />我确认本次会向生产环境持续发送真实请求</label>
      <LoadMonitoringSelector v-if="!preset" v-model="monitoring" :environment-revision-id="environmentId" />
      <div class="load-review-box"><strong>执行前预估</strong><p>{{ iterationEstimate }} 持续 {{ totalDuration }} 秒；选择 {{ selected.length }} 台节点；当前可用 {{ selectedCapacity.vus }} VU / {{ selectedCapacity.rate }} 次/秒。创建后还需依次完成目标连通性检查、单用户预检和开始执行。</p></div>
    </div>
    <footer><button class="secondary-command" type="button" @click="emit('cancel')">返回执行列表</button><span /><button data-testid="load-run-submit" class="primary-command" type="button" :disabled="!canSubmit" @click="submit">创建压测草稿</button></footer>
  </section>
</template>

<style scoped>
.load-wizard-body > label.load-check{display:flex;align-items:center;gap:8px;line-height:1.5}
.load-wizard-body > label.load-check input[type=checkbox]{width:18px;height:18px;min-height:18px;flex:0 0 18px;margin:0;padding:0}
.load-review-box ul{margin:8px 0;padding-left:20px;font-size:13px;line-height:1.7}

.load-stage-editor{display:grid;gap:10px;border:1px solid #dce5ee;padding:12px;border-radius:8px}.load-stage-row{display:grid;grid-template-columns:auto minmax(70px,1fr) minmax(70px,1fr) auto;gap:10px;align-items:center}.load-stage-row label{display:grid;gap:4px}.load-stage-row input{min-width:0;padding:8px;box-sizing:border-box}.load-stage-editor p{margin:0;font-size:13px;color:#617086}
</style>
