<script setup lang="ts">
import { computed, ref } from 'vue'
import type { EnvironmentRevisionOption, LoadAgent, LoadScenario } from '../api/contracts'
import { apiTestingHasPermission } from '../utils/authRedirect'

type Executor = 'constant-vus' | 'ramping-vus' | 'constant-arrival-rate' | 'ramping-arrival-rate'

const props = defineProps<{ scenario: LoadScenario; environments: EnvironmentRevisionOption[]; agents: LoadAgent[] }>()
const emit = defineEmits<{ submit: [payload: Record<string, unknown>]; cancel: [] }>()
const environmentId = ref(props.environments[0]?.id || '')
const executor = ref<Executor>('constant-vus')
const vus = ref(20)
const rate = ref(50)
const duration = ref(60)
const p95 = ref(500)
const selectedIds = ref<string[]>([])
const allowFallback = ref(false)
const allowRunAnyway = ref(false)
const priority = ref('normal')
const productionConfirmed = ref(false)

const validAgents = computed(() => props.agents.filter(item => item.status === 'online' && item.calibration_state === 'valid' && (allowFallback.value || item.scheduling_tier !== 'fallback')))
const selected = computed(() => props.agents.filter(item => selectedIds.value.includes(item.id)))
const environment = computed(() => props.environments.find(item => item.id === environmentId.value))
const production = computed(() => /生产|正式|prod(uction)?/i.test(environment.value?.name || ''))
const hasProductionPermission = computed(() => apiTestingHasPermission('api.production'))
const arrivalModel = computed(() => executor.value.includes('arrival-rate'))
const targetIterations = computed(() => rate.value * duration.value)
const iterationEstimate = computed(() => arrivalModel.value
  ? `按目标吞吐预计约 ${targetIterations.value} 次完整链路。`
  : `${executor.value === 'constant-vus' ? '固定并发' : '阶梯并发'}会在时长内持续循环；实际次数取决于接口响应时间，不能按 VU × 秒数推算。`)

function availableCapacity(agent: LoadAgent, field: 'max_vus' | 'max_iterations_per_second'): number {
  const candidates = [agent.hard_limits[field], agent.soft_limits[field], agent.health.calibration?.[field]]
    .map(value => Number(value || 0)).filter(value => value > 0)
  const limit = candidates.length ? Math.min(...candidates) : 0
  const usageKey = field === 'max_vus' ? 'vus' : 'iterations_per_second'
  return Math.max(0, Math.floor(limit - Number(agent.current_usage[usageKey] || 0)))
}

const selectedCapacity = computed(() => selected.value.reduce((total, agent) => ({
  vus: total.vus + availableCapacity(agent, 'max_vus'),
  rate: total.rate + availableCapacity(agent, 'max_iterations_per_second'),
}), { vus: 0, rate: 0 }))
const capacityEnough = computed(() => arrivalModel.value
  ? selectedCapacity.value.rate >= rate.value && selectedCapacity.value.vus >= vus.value
  : selectedCapacity.value.vus >= vus.value)
const productionReady = computed(() => !production.value || (hasProductionPermission.value && productionConfirmed.value))
const canSubmit = computed(() => Boolean(
  environmentId.value && props.scenario.active_version_id && selected.value.length
  && productionReady.value && (capacityEnough.value || allowRunAnyway.value),
))

function selectExecutor(value: string): void { executor.value = value as Executor }
function toggleAgent(id: string, checked: boolean): void {
  selectedIds.value = checked ? [...new Set([...selectedIds.value, id])] : selectedIds.value.filter(item => item !== id)
}
function workload(): Record<string, unknown> {
  if (executor.value === 'constant-vus') return { executor: executor.value, vus: vus.value, duration_seconds: duration.value }
  if (executor.value === 'ramping-vus') return { executor: executor.value, start_vus: 1, stages: [{ duration_seconds: duration.value, target: vus.value }] }
  if (executor.value === 'constant-arrival-rate') return {
    executor: executor.value, rate: rate.value, time_unit: '1s', duration_seconds: duration.value,
    pre_allocated_vus: Math.max(1, vus.value), max_vus: Math.max(vus.value, 100),
  }
  return {
    executor: executor.value, start_rate: 1, time_unit: '1s',
    pre_allocated_vus: Math.max(1, vus.value), max_vus: Math.max(vus.value, 100),
    stages: [{ duration_seconds: duration.value, target: rate.value }],
  }
}
function submit(): void {
  emit('submit', {
    scenario_version_id: props.scenario.active_version_id,
    environment_revision_id: environmentId.value,
    workload: workload(),
    thresholds: { p95_ms: { operator: 'less_than_or_equal', value: p95.value, required: true } },
    priority: priority.value,
    allocation_policy: {
      allow_fallback: allowFallback.value,
      allow_run_anyway: allowRunAnyway.value,
      agent_ids: selectedIds.value,
    },
  })
}
</script>

<template>
  <section class="load-wizard" aria-label="创建压测执行">
    <header><div><p class="eyebrow">压测配置</p><h2>{{ scenario.name }}</h2></div><button class="text-command" type="button" @click="emit('cancel')">取消</button></header>
    <div class="load-wizard-body">
      <label>目标环境<select v-model="environmentId"><option v-for="item in environments" :key="item.id" :value="item.id">{{ item.name }} · v{{ item.revision }}</option></select></label>
      <p v-if="production && !hasProductionPermission" class="load-warning">当前账号没有 api.production 权限，不能创建生产环境压测。请联系管理员授权。</p>
      <p v-else-if="production" class="load-warning">生产环境会持续收到真实请求，请确认范围、数据和停止条件。</p>

      <h3>负载模型</h3>
      <div class="load-option-grid">
        <button v-for="item in [{ value: 'constant-vus', label: '固定并发', help: '保持固定虚拟用户，VU 不等于 QPS。' }, { value: 'ramping-vus', label: '阶梯并发', help: '逐步升高并发，观察性能拐点。' }, { value: 'constant-arrival-rate', label: '固定吞吐', help: '保持每秒完整链路次数，适合验证目标 QPS。' }, { value: 'ramping-arrival-rate', label: '阶梯吞吐', help: '逐级提高吞吐，寻找容量上限。' }]" :key="item.value" :data-testid="`load-model-${item.value}`" :class="{ active: executor === item.value }" type="button" @click="selectExecutor(item.value)"><strong>{{ item.label }}</strong><span>{{ item.help }}</span></button>
      </div>
      <p class="load-model-guide"><strong>第一次怎么选：</strong>只想确认流程时，使用安全的只读接口和“固定吞吐”，先从 1 次/秒、10 秒开始；“固定并发”用于模拟同时在线用户，不会把请求速度限制为 VU 数。</p>
      <div class="load-field-grid">
        <label v-if="!arrivalModel">并发用户（VU）<input v-model.number="vus" data-testid="load-vus" type="number" min="1" /></label>
        <template v-else><label>目标吞吐（次/秒）<input v-model.number="rate" data-testid="load-rate" type="number" min="1" /></label><label>预分配并发（VU）<input v-model.number="vus" data-testid="load-vus" type="number" min="1" /></label></template>
        <label>持续时间（秒）<input v-model.number="duration" data-testid="load-duration" type="number" min="1" /></label>
        <label>P95 响应上限（毫秒）<input v-model.number="p95" data-testid="load-p95" type="number" min="1" /></label>
        <label>排队优先级<select v-model="priority"><option value="urgent">紧急（优先排队，不抢占运行任务）</option><option value="high">高</option><option value="normal">普通（日常默认）</option><option value="low">低</option></select></label>
      </div>

      <h3>选择压测节点</h3>
      <p v-if="!validAgents.length" class="load-warning">没有可用的已校准节点。请先到“压测节点”完成校准。</p>
      <div class="load-agent-options"><label v-for="item in agents" :key="item.id"><input :data-testid="`load-agent-${item.id}`" type="checkbox" :disabled="item.status !== 'online' || item.calibration_state !== 'valid' || (item.scheduling_tier === 'fallback' && !allowFallback)" :checked="selectedIds.includes(item.id)" @change="toggleAgent(item.id, ($event.target as HTMLInputElement).checked)" /><span><strong>{{ item.name }}</strong><small v-if="item.calibration_state !== 'valid'">{{ item.calibration_state === 'expired' ? '校准过期，不能选择' : '未完成有效校准，不能选择' }}</small><small v-else>当前可用 {{ availableCapacity(item, 'max_vus') }} VU / {{ availableCapacity(item, 'max_iterations_per_second') }} 次/秒</small></span></label></div>
      <label class="load-check"><input v-model="allowFallback" type="checkbox" />允许备用节点参与（本机备用节点默认不参与）</label>
      <p v-if="selected.length && !capacityEnough" class="load-warning" data-testid="capacity-shortfall">所选节点容量不足：合计可用 {{ selectedCapacity.vus }} VU / {{ selectedCapacity.rate }} 次/秒。请降低目标或增加节点。</p>
      <label class="load-check"><input v-model="allowRunAnyway" data-testid="allow-run-anyway" type="checkbox" />容量不足时仍创建任务（报告固定标记为证据不足）</label>
      <label v-if="production && hasProductionPermission" class="load-check"><input v-model="productionConfirmed" type="checkbox" />我确认本次会向生产环境持续发送真实请求</label>
      <div class="load-review-box"><strong>执行前预估</strong><p>{{ iterationEstimate }} 持续 {{ duration }} 秒；选择 {{ selected.length }} 台节点；当前可用 {{ selectedCapacity.vus }} VU / {{ selectedCapacity.rate }} 次/秒。创建后还需依次完成目标连通性检查、单用户预检和开始执行。</p></div>
    </div>
    <footer><button class="secondary-command" type="button" @click="emit('cancel')">取消</button><span /><button data-testid="load-run-submit" class="primary-command" type="button" :disabled="!canSubmit" @click="submit">创建压测草稿</button></footer>
  </section>
</template>
