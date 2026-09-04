<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { Copy, Plus, RefreshCw, Server, SlidersHorizontal } from 'lucide-vue-next'

import type { LoadAgent, LoadAgentEnrollmentResult, LoadCalibrationState, LoadCapacityLimits, LoadSchedulingTier } from '../api/contracts'
import { useLoadTestingStore } from '../stores/loadTesting'
import { apiTestingHasPermission } from '../utils/authRedirect'

const store = useLoadTestingStore()
const query = ref('')
const enrollmentOpen = ref(false)
const enrollment = ref<LoadAgentEnrollmentResult | null>(null)
const enrollmentName = ref('')
const enrollmentGroup = ref('')
const enrollmentTier = ref<LoadSchedulingTier>('preferred')
const feedback = ref('')
const capacityAgent = ref<LoadAgent | null>(null)
const capacityDraft = ref<LoadCapacityLimits | null>(null)
const canManage = apiTestingHasPermission('api.loadtest.manage_agents')
let refreshTimer: ReturnType<typeof setTimeout> | null = null

const filteredAgents = computed(() => {
  const keyword = query.value.trim().toLocaleLowerCase('zh-CN')
  if (!keyword) return store.agents
  return store.agents.filter(item => [item.name, item.node_group, item.egress_ip, tier(item.scheduling_tier).label]
    .some(value => String(value || '').toLocaleLowerCase('zh-CN').includes(keyword)))
})

const agentSummary = computed(() => {
  const online = store.agents.filter(item => item.status === 'online')
  const schedulable = online.filter(item => item.scheduling_tier !== 'disabled'
    && item.calibration_state === 'valid' && item.health.schedulable !== false)
  return {
    total: store.agents.length,
    online: online.length,
    schedulable: schedulable.length,
    calibratedVus: schedulable.reduce((sum, item) => sum + Number(item.health.calibration?.max_vus || 0), 0),
    calibratedRate: schedulable.reduce((sum, item) => sum + Number(item.health.calibration?.max_iterations_per_second || 0), 0),
    vus: schedulable.reduce((sum, item) => sum + availableCapacity(item, 'max_vus'), 0),
    rate: schedulable.reduce((sum, item) => sum + availableCapacity(item, 'max_iterations_per_second'), 0),
  }
})

function scheduleRefresh(): void {
  if (refreshTimer) clearTimeout(refreshTimer)
  refreshTimer = setTimeout(async () => {
    refreshTimer = null
    await store.loadAgents(true)
    scheduleRefresh()
  }, 3000)
}

function capacityCeiling(item: LoadAgent, field: 'max_vus' | 'max_iterations_per_second'): number {
  return Math.min(
    Number(item.hard_limits[field] || 0),
    Number(item.soft_limits[field] || 0),
    Number(item.health.calibration?.[field] || 0),
  )
}

function capacityLimiter(item: LoadAgent, field: 'max_vus' | 'max_iterations_per_second'): string {
  const value = capacityCeiling(item, field)
  if (value === Number(item.soft_limits[field] || 0)) return '平台容量策略'
  if (value === Number(item.health.calibration?.[field] || 0)) return '校准达到值'
  return '本机硬上限'
}

onMounted(async () => {
  await store.loadAgents()
  scheduleRefresh()
})
onBeforeUnmount(() => {
  if (refreshTimer) clearTimeout(refreshTimer)
  refreshTimer = null
})

function tier(value: LoadSchedulingTier): { label: string; help: string } {
  return {
    preferred: { label: '首选节点', help: '优先承接压测，适合专用压测服务器。' },
    normal: { label: '普通节点', help: '首选容量不足时参与，适合共享但稳定的服务器。' },
    fallback: { label: '备用节点', help: '只有明确允许备用节点时参与；本机备用节点不会自动参与。' },
    disabled: { label: '已停用', help: '不再接收任务，重新使用时需注册新节点。' },
  }[value]
}

function calibration(value: LoadCalibrationState): { label: string; help: string } {
  return {
    uncalibrated: { label: '未校准', help: '先执行本地容量测量，才能用于正式压测。' },
    calibrating: { label: '校准中', help: 'Agent 正在本机运行 k6 测量，不访问业务环境。' },
    valid: { label: '校准有效', help: '容量证据有效，可以参与预检和压测。' },
    expired: { label: '校准过期', help: '校准超过七天，请重新校准。' },
    invalidated: { label: '配置变化后失效', help: 'Agent、k6 或硬件变化，请重新校准。' },
    failed: { label: '校准失败', help: '检查 k6、CPU/内存限制和 Agent 日志后重试。' },
  }[value]
}

function heartbeat(item: LoadAgent): { label: string; state: string } {
  if (item.scheduling_tier === 'disabled') return { label: '节点已停用', state: 'disabled' }
  if (item.status === 'online') return { label: '心跳正常', state: 'online' }
  return { label: '心跳中断', state: 'offline' }
}

function availableCapacity(item: LoadAgent, field: 'max_vus' | 'max_iterations_per_second'): number {
  const measured = capacityCeiling(item, field)
  const used = field === 'max_vus'
    ? Number(item.current_usage.vus || 0)
    : Number(item.current_usage.iterations_per_second || 0)
  return Math.max(0, measured - used)
}

function openCapacity(item: LoadAgent): void {
  capacityAgent.value = item
  capacityDraft.value = { ...item.soft_limits }
  feedback.value = ''
}

function applyCapacityPreset(ratio: number): void {
  if (!capacityAgent.value) return
  const hard = capacityAgent.value.hard_limits
  capacityDraft.value = {
    max_processes: Math.max(1, Math.floor(hard.max_processes * ratio)),
    max_vus: Math.max(1, Math.floor(hard.max_vus * ratio)),
    max_iterations_per_second: Math.max(1, Math.floor(hard.max_iterations_per_second * ratio)),
    max_duration_seconds: hard.max_duration_seconds,
    cpu_cores: Math.max(1, Math.floor(hard.cpu_cores * ratio)),
    memory_mb: Math.max(256, Math.floor(hard.memory_mb * ratio)),
  }
}

async function saveCapacity(): Promise<void> {
  if (!capacityAgent.value || !capacityDraft.value) return
  const hard = capacityAgent.value.hard_limits
  const invalid = (Object.keys(hard) as Array<keyof LoadCapacityLimits>).find(key => {
    const value = Number(capacityDraft.value?.[key] || 0)
    return !Number.isFinite(value) || value <= 0 || value > Number(hard[key])
  })
  if (invalid) {
    feedback.value = '平台容量策略必须为正数，且不能超过页面显示的本机硬上限。'
    return
  }
  const name = capacityAgent.value.name
  try {
    await store.updateAgent(capacityAgent.value.id, { soft_limits: { ...capacityDraft.value } })
    capacityAgent.value = null
    capacityDraft.value = null
    feedback.value = `“${name}”的平台容量策略已保存，无需登录服务器。`
  } catch { /* store keeps the server explanation */ }
}

function calibrationDisabledReason(item: LoadAgent): string {
  if (!canManage) return '需要节点管理权限，请联系管理员授权。'
  if (item.status !== 'online') return '节点离线，请先启动 Agent 并等待心跳恢复。'
  if (Number(item.current_usage.processes || 0) > 0) return '节点正在执行压测，请等待任务结束。'
  if (item.calibration_state === 'calibrating') return '节点正在校准，请等待结果回传。'
  return ''
}

async function changeTier(item: LoadAgent, event: Event): Promise<void> {
  feedback.value = ''
  const value = (event.target as HTMLSelectElement).value as LoadSchedulingTier
  try {
    await store.updateAgent(item.id, { scheduling_tier: value })
    feedback.value = `“${item.name}”已调整为${tier(value).label}`
  } catch { /* store keeps the server explanation */ }
}

async function requestCalibration(item: LoadAgent): Promise<void> {
  const reason = calibrationDisabledReason(item)
  if (reason) { feedback.value = reason; return }
  feedback.value = ''
  try {
    await store.calibrateAgent(item.id)
    feedback.value = `已通知“${item.name}”开始校准，页面刷新后可查看结果。`
  } catch { /* store keeps the server explanation */ }
}

function openEnrollment(): void {
  enrollmentOpen.value = true
  enrollment.value = null
  enrollmentName.value = ''
  enrollmentGroup.value = ''
  enrollmentTier.value = 'preferred'
  feedback.value = ''
}

async function createEnrollment(): Promise<void> {
  if (!enrollmentName.value.trim()) { feedback.value = '请填写节点名称'; return }
  try {
    enrollment.value = await store.createEnrollment({
      name: enrollmentName.value.trim(), node_group: enrollmentGroup.value.trim(),
      scheduling_tier: enrollmentTier.value, expires_in_seconds: 900,
    })
    feedback.value = '注册令牌已创建，请现在复制并在目标服务器执行。'
  } catch { /* store keeps the server explanation */ }
}

function shellQuote(value: string): string {
  return `'${value.split("'").join("'\\''")}'`
}

const enrollmentCommand = computed(() => {
  if (!enrollment.value) return ''
  const platformUrl = window.location.origin
  const privateTransport = window.location.protocol === 'http:' ? " ALLOW_INSECURE_PRIVATE_AGENT_TRANSPORT='1'" : ''
  return `PLATFORM_URL=${shellQuote(platformUrl)} ENROLL_TOKEN=${shellQuote(enrollment.value.token)}${privateTransport} bash deploy/load-agent/install.sh`
})

async function copyCommand(): Promise<void> {
  try {
    await navigator.clipboard.writeText(enrollmentCommand.value)
    feedback.value = '启动命令已复制'
  } catch {
    feedback.value = '浏览器未允许复制，请手动选中命令复制。'
  }
}

function closeEnrollment(): void {
  enrollmentOpen.value = false
  enrollment.value = null
}

function dateTime(value?: string | null): string {
  if (!value) return '暂无'
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString('zh-CN', { hour12: false })
}
</script>

<template>
  <section class="workspace load-agents-page" data-testid="load-agents-page">
    <header class="page-toolbar load-page-toolbar">
      <div><p class="eyebrow">性能测试</p><h1>压测节点</h1><p class="page-subtitle">先校准节点容量，再创建压测。平台按调度级别和实测容量选择节点。</p></div>
      <div class="load-toolbar-actions">
        <button data-testid="load-agents-refresh" class="secondary-command" type="button" :disabled="store.loadingAgents" @click="store.loadAgents()"><RefreshCw :size="15" />刷新</button>
        <button v-if="canManage" data-testid="load-agent-enroll-open" class="primary-command" type="button" @click="openEnrollment"><Plus :size="15" />注册节点</button>
      </div>
    </header>

    <div class="load-agent-guide">
      <Server :size="18" />
      <div><strong>节点怎么选</strong><span>专用服务器设为“首选”；资源充足且压测期间没有其他重任务的服务器可设为“普通”；需要优先保护平台服务时设为“备用”。心跳、校准结果和容量每 3 秒自动更新。</span></div>
    </div>
    <div v-if="store.agents.length" data-testid="load-agent-summary" class="load-agent-summary" aria-label="压测节点实时汇总">
      <div><span>节点总数</span><strong>{{ agentSummary.total }}</strong><small>已注册的全部执行节点</small></div>
      <div><span><i class="load-status-dot online pulse" />心跳正常</span><strong>{{ agentSummary.online }}</strong><small>正在持续连接平台</small></div>
      <div><span>可调度</span><strong>{{ agentSummary.schedulable }}</strong><small>在线且校准有效</small></div>
      <div><span>校准达到</span><strong>{{ agentSummary.calibratedVus }} VU</strong><small>{{ agentSummary.calibratedRate }} 次/秒，本地校准达到值汇总</small></div>
      <div class="capacity-primary"><span>当前可分配</span><strong>{{ agentSummary.vus }} VU</strong><small>{{ agentSummary.rate }} 次/秒，三项取最小值后减当前占用</small></div>
    </div>
    <label class="search-box load-agent-search"><span class="sr-only">搜索压测节点</span><input v-model="query" data-testid="load-agent-search" type="search" placeholder="搜索节点名称、分组或出口 IP" /></label>

    <p v-if="store.agentError" role="alert" class="state-message state-error">{{ store.agentError }}</p>
    <p v-else-if="store.loadingAgents" class="state-message">正在读取压测节点…</p>
    <div v-else-if="!store.agents.length" class="management-empty">
      <Server :size="28" /><h2>还没有压测节点</h2><p>创建一次性注册令牌，在压测服务器启动 Agent；注册成功后先校准，再执行压测。</p>
    </div>
    <p v-else-if="!filteredAgents.length" class="section-empty">没有匹配的节点，请修改搜索条件。</p>
    <div v-else class="load-agent-grid">
      <article v-for="item in filteredAgents" :key="item.id" :data-testid="`load-agent-card-${item.id}`" class="load-agent-card">
        <header>
          <div class="load-agent-identity"><div><h2>{{ item.name }}</h2><small>{{ item.node_group || '未分组' }} · {{ item.egress_ip || '未上报出口 IP' }}</small></div><span :data-testid="`load-agent-heartbeat-${item.id}`" :class="['load-heartbeat', heartbeat(item).state]"><i :class="['load-status-dot', heartbeat(item).state, { pulse: heartbeat(item).state === 'online' }]" />{{ heartbeat(item).label }}</span></div>
          <b :class="`calibration-${item.calibration_state}`">{{ calibration(item.calibration_state).label }}</b>
        </header>
        <p class="agent-help">{{ calibration(item.calibration_state).help }}</p>
        <p v-if="item.calibration_state === 'failed' && item.health.calibration?.message" class="agent-calibration-error" role="alert">失败原因：{{ item.health.calibration.message }}</p>
        <div class="load-capacity-grid">
          <div><span>本机硬上限</span><strong>{{ item.hard_limits.max_vus }} VU · {{ item.hard_limits.max_iterations_per_second }} 次/秒</strong><small>进程 {{ item.hard_limits.max_processes }} · 最长 {{ item.hard_limits.max_duration_seconds }} 秒 · CPU {{ item.hard_limits.cpu_cores }} 核 · 内存 {{ item.hard_limits.memory_mb }} MB。Agent按容器资源上报，平台不能调高。</small></div>
          <div><span>平台容量策略（页面可配）</span><strong>{{ item.soft_limits.max_vus }} VU · {{ item.soft_limits.max_iterations_per_second }} 次/秒</strong><small>任务不会超过此保护值；管理员可直接在本页修改。</small></div>
          <div><span>校准达到值</span><strong>{{ item.health.calibration?.max_vus || 0 }} VU · {{ item.health.calibration?.max_iterations_per_second || 0 }} 次/秒</strong><small>本地校准端点达到的档位，不代表业务接口容量。有效至 {{ dateTime(item.health.calibration?.valid_until) }}</small></div>
          <div class="capacity-primary"><span>当前可分配</span><strong>{{ availableCapacity(item, 'max_vus') }} VU · {{ availableCapacity(item, 'max_iterations_per_second') }} 次/秒</strong><small>VU 受{{ capacityLimiter(item, 'max_vus') }}限制，吞吐受{{ capacityLimiter(item, 'max_iterations_per_second') }}限制；三项取最小值后减占用。</small></div>
          <div><span>当前占用</span><strong>{{ item.current_usage.processes || 0 }} 进程 · {{ item.current_usage.vus || 0 }} VU</strong><small>最后心跳 {{ dateTime(item.last_heartbeat_at) }}</small></div>
        </div>
        <div class="load-agent-controls">
          <label><span>调度级别（节点参与顺序）</span><select :data-testid="`agent-tier-${item.id}`" :value="item.scheduling_tier" :disabled="!canManage || item.scheduling_tier === 'disabled' || store.mutating" @change="changeTier(item, $event)"><option value="preferred">首选节点</option><option value="normal">普通节点</option><option value="fallback">备用节点</option><option value="disabled">停用节点</option></select><small>{{ tier(item.scheduling_tier).help }}</small></label>
          <div class="load-agent-buttons"><button v-if="canManage" :data-testid="`agent-capacity-open-${item.id}`" class="secondary-command" type="button" :disabled="store.mutating" @click="openCapacity(item)"><SlidersHorizontal :size="15" />配置容量策略</button><button :data-testid="`agent-calibrate-${item.id}`" class="secondary-command" type="button" :disabled="Boolean(calibrationDisabledReason(item)) || store.mutating" :title="calibrationDisabledReason(item) || '重新测量本机k6容量'" @click="requestCalibration(item)"><SlidersHorizontal :size="15" />{{ item.calibration_state === 'calibrating' ? '正在校准' : '校准节点' }}</button></div>
        </div>
        <p v-if="calibrationDisabledReason(item)" class="agent-disabled-reason">{{ calibrationDisabledReason(item) }}</p>
      </article>
    </div>
    <p v-if="feedback" class="load-feedback" aria-live="polite">{{ feedback }}</p>

    <div v-if="capacityAgent && capacityDraft" class="load-dialog-backdrop" @click.self="capacityAgent = null">
      <section class="load-dialog" role="dialog" aria-modal="true" aria-labelledby="capacity-title">
        <header><div><p class="eyebrow">页面配置</p><h2 id="capacity-title">配置平台容量策略</h2></div><button class="text-command" type="button" @click="capacityAgent = null">关闭</button></header>
        <p class="agent-help">{{ capacityAgent.name }} · 无需登录服务器。策略值只能降低 Agent 上报的本机硬上限，不会篡改校准结果。</p>
        <div class="capacity-presets"><button data-testid="capacity-preset-protected" type="button" @click="applyCapacityPreset(.25)">保守 25%</button><button data-testid="capacity-preset-standard" type="button" @click="applyCapacityPreset(.6)">标准 60%</button><button data-testid="capacity-preset-dedicated" type="button" @click="applyCapacityPreset(.8)">专用 80%</button></div>
        <div class="capacity-form-grid">
          <label>最大进程<input v-model.number="capacityDraft.max_processes" type="number" min="1" :max="capacityAgent.hard_limits.max_processes" /></label>
          <label>最大 VU<input v-model.number="capacityDraft.max_vus" data-testid="capacity-soft-vus" type="number" min="1" :max="capacityAgent.hard_limits.max_vus" /></label>
          <label>最大吞吐（次/秒）<input v-model.number="capacityDraft.max_iterations_per_second" type="number" min="1" :max="capacityAgent.hard_limits.max_iterations_per_second" /></label>
          <label>最长任务（秒）<input v-model.number="capacityDraft.max_duration_seconds" type="number" min="1" :max="capacityAgent.hard_limits.max_duration_seconds" /></label>
          <label>最多 CPU（核）<input v-model.number="capacityDraft.cpu_cores" type="number" min="1" :max="capacityAgent.hard_limits.cpu_cores" /></label>
          <label>最多内存（MB）<input v-model.number="capacityDraft.memory_mb" type="number" min="256" :max="capacityAgent.hard_limits.memory_mb" /></label>
        </div>
        <p class="load-warning">修改的是平台调度保护值。提高到现有校准值以内无需重新校准；Agent 硬件、容器 CPU/内存或 k6 版本变化后才需要重新校准。</p>
        <button data-testid="capacity-soft-save" class="primary-command" type="button" :disabled="store.mutating" @click="saveCapacity">保存容量策略</button>
      </section>
    </div>

    <div v-if="enrollmentOpen" class="load-dialog-backdrop" @click.self="closeEnrollment">
      <section class="load-dialog" role="dialog" aria-modal="true" aria-labelledby="enrollment-title">
        <header><div><p class="eyebrow">一次性注册</p><h2 id="enrollment-title">连接新的压测节点</h2></div><button data-testid="enrollment-close" class="text-command" type="button" @click="closeEnrollment">关闭</button></header>
        <template v-if="!enrollment">
          <p class="agent-help">服务器不需要安装 Git 或拉取整个平台。先上传并解压最小 Agent 包，再生成令牌。</p>
          <label>节点名称<input v-model="enrollmentName" data-testid="enrollment-name" placeholder="例如：腾讯云压测节点 1" /></label>
          <label>节点分组<input v-model="enrollmentGroup" data-testid="enrollment-group" placeholder="例如：腾讯云上海" /></label>
          <label>初始调度级别<select v-model="enrollmentTier" data-testid="enrollment-tier"><option value="preferred">首选节点（专用服务器）</option><option value="normal">普通节点（共享服务器）</option><option value="fallback">备用节点（需任务明确允许）</option></select></label>
          <button data-testid="enrollment-submit" class="primary-command" type="button" :disabled="store.mutating" @click="createEnrollment">生成 15 分钟注册令牌</button>
        </template>
        <div v-else data-testid="enrollment-result" class="enrollment-result">
          <strong>令牌只显示这一次</strong>
          <p>有效至 {{ dateTime(enrollment.expires_at) }}。在目标服务器解压后的 Agent 包根目录执行：</p>
          <pre>{{ enrollmentCommand }}</pre>
          <p class="transport-warning">当前平台仍是 HTTP。只有受控私网/VPN可设置允许不安全传输；跨公网部署前必须配置 HTTPS。</p>
          <button data-testid="enrollment-copy" class="primary-command" type="button" @click="copyCommand"><Copy :size="15" />复制启动命令</button>
        </div>
      </section>
    </div>
  </section>
</template>
