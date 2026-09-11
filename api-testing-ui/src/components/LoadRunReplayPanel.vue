<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import type { LoadReport, LoadRun } from '../api/contracts'

type UnknownRecord = Record<string, unknown>
interface ReplayWindow {
  index: number
  start: number
  end: number
  duration: number
  requests: number
  iterations: number
  http_failures: number
  business_failures: number
  p95: number | null
}

interface LinePoint {
  time: number
  value: number
}

interface LineSeries {
  id: string
  name: string
  unit: string
  color: string
  points: LinePoint[]
}

interface ReplayEvent {
  id: string
  time: number
  label: string
  summary: string
  details: string[]
  evidence: string[]
}

const props = defineProps<{ run?: LoadRun | null; report: LoadReport }>()

const speed = ref(1)
const playing = ref(false)
const cursor = ref(0)
const timer = ref<ReturnType<typeof setInterval> | null>(null)
const now = ref(Date.now() / 1000)
const reducedMotion = ref(false)
const mediaQuery = ref<MediaQueryList | null>(null)
const reducedMotionChangeHandler = (event: MediaQueryListEvent): void => {
  reducedMotion.value = event.matches
  if (event.matches && playing.value) stop()
}

const keyboardHint = computed(() => reducedMotion.value
  ? '已开启减少动态偏好：Space=播放/暂停，←/→=跳窗，Home/End=首尾，鼠标拖动窗口更稳。'
  : 'Space=播放/暂停，←/→=跳窗，Home/End=首尾，1/2/5/0=速度。'
)

const available = computed(() => replayWindows.value.length >= 1 && replayWindows.value.some(item => item.requests > 0 || item.p95 !== null))

function toRecord(value: unknown): UnknownRecord { return value && typeof value === 'object' ? value as UnknownRecord : {} }
function toFiniteNumber(value: unknown): number | null {
  const numeric = Number(value)
  return Number.isFinite(numeric) ? numeric : null
}
function formatNumber(value: number | null | undefined, unit = ''): string { return value == null ? '—' : `${value.toLocaleString('zh-CN')}${unit ? ` ${unit}` : ''}` }
function formatRate(value: number | null | undefined): string { return value == null ? '—' : `${(value * 100).toLocaleString('zh-CN', { maximumFractionDigits: 2 })}%` }
function formatDuration(seconds: number): string {
  const total = Math.max(0, Math.floor(seconds))
  const hour = Math.floor(total / 3600)
  const min = Math.floor(total / 60) % 60
  const sec = total % 60
  return `${String(hour).padStart(2, '0')}:${String(min).padStart(2, '0')}:${String(sec).padStart(2, '0')}`
}
function parseTime(value: unknown): number | null {
  if (typeof value === 'number' && Number.isFinite(value)) return value
  const text = String(value || '')
  if (!text) return null
  const parsed = Date.parse(text)
  return Number.isNaN(parsed) ? null : parsed / 1000
}
function inferWindowGap(points: ReplayWindow[]): number {
  if (points.length < 2) return 5
  const gaps: number[] = []
  for (let index = 1; index < points.length; index++) {
    const gap = points[index].start - points[index - 1].start
    if (Number.isFinite(gap) && gap > 0) gaps.push(gap)
  }
  if (!gaps.length) return 5
  gaps.sort((left, right) => left - right)
  return gaps[Math.floor(gaps.length / 2)]
}

const runConfig = computed(() => toRecord(props.run?.configuration))
const workload = computed(() => toRecord(runConfig.value.workload) || (toRecord(props.report.evidence?.workload_snapshot)))
const loadModel = computed(() => String(props.run?.load_model || props.report.load_goal?.model || ''))
const runStarted = computed(() => parseTime(props.run?.started_at) || parseTime(props.report.evidence?.scenario_snapshot?.started_at) || parseTime((props.report.series || [])[0]?.started_at))
const runFinished = computed(() => parseTime(props.run?.finished_at) || parseTime(props.report.evidence?.workload_snapshot?.finished_at))

const replayStages = computed(() => {
  const source = toRecord(props.report.load_goal).stages
  const stages = Array.isArray(source) ? source as UnknownRecord[] : Array.isArray(workload.value.stages) ? workload.value.stages as UnknownRecord[] : []
  let offset = 0
  return stages
    .map((stage, index) => {
      const duration = Math.max(0, toFiniteNumber(stage.duration_seconds) || 0)
      const declaredStart = toFiniteNumber(stage.start_seconds)
      const start = declaredStart == null ? offset : declaredStart
      const target = toFiniteNumber(stage.target_vus) || toFiniteNumber(stage.target_rate) || toFiniteNumber(stage.target) || null
      const startVus = toFiniteNumber(stage.start_vus)
      const startRate = toFiniteNumber(stage.start_rate)
      const startPressure = loadModel.value.includes('vus') ? (startVus || startRate) : (startRate || startVus)
      if (Number.isFinite(start) && Number.isFinite(duration)) offset = start + duration
      return {
        index: index + 1,
        start,
        duration,
        target,
        startPressure,
      }
    })
    .filter(item => Number.isFinite(item.start))
})

const p95Reference = computed(() => {
  const threshold = (props.report.thresholds || []).find(item => item.key === 'p95_ms')
  const value = toFiniteNumber(threshold?.expected)
  return value == null ? null : value
})

const replayWindows = computed<ReplayWindow[]>(() => {
  const raw = Array.isArray(props.report.series) ? props.report.series : []
  const parsed = raw
    .map((item: UnknownRecord, index): ReplayWindow | null => {
      const start = parseTime(item.started_at)
      if (start == null) return null
      return {
        index,
        start,
        end: start,
        duration: 5,
        requests: Math.max(0, toFiniteNumber(item.requests) || 0),
        iterations: Math.max(0, toFiniteNumber(item.iterations) || 0),
        http_failures: Math.max(0, toFiniteNumber(item.http_failures) || 0),
        business_failures: Math.max(0, toFiniteNumber(item.business_failures) || 0),
        p95: toFiniteNumber(item.p95_ms),
      }
    })
    .filter((item): item is ReplayWindow => item !== null)
    .sort((left, right) => left.start - right.start)

  if (!parsed.length) return []

  const gap = inferWindowGap(parsed)
  return parsed.map((item, index) => {
    const next = parsed[index + 1]
    const duration = (next?.start || item.start + gap) - item.start
    return {
      ...item,
      duration: duration > 0 ? duration : gap,
      end: item.start + (duration > 0 ? duration : gap),
    }
  })
})

const startTime = computed(() => runStarted.value ?? replayWindows.value[0]?.start ?? now.value)
const endTime = computed(() => runFinished.value ?? replayWindows.value[replayWindows.value.length - 1]?.end ?? startTime.value + (inferWindowGap(replayWindows.value) || 5))
const timelineSpan = computed(() => Math.max(1, endTime.value - startTime.value))
const cursorWindowIndex = computed(() => {
  const max = Math.max(1, replayWindows.value.length)
  if (!Number.isFinite(cursor.value)) return 0
  const normalized = Math.min(Math.max(0, cursor.value), max - 1)
  return Math.floor(normalized)
})

const currentWindow = computed(() => replayWindows.value[cursorWindowIndex.value] || null)
const cursorWindowTime = computed(() => currentWindow.value?.start ?? startTime.value)
const cursorOffsetText = computed(() => formatDuration(Math.max(0, cursorWindowTime.value - (runStarted.value ?? cursorWindowTime.value))))
const cursorClockText = computed(() => new Date(cursorWindowTime.value * 1000).toLocaleTimeString('zh-CN', { hour12: false }))

const planPressureSeries = computed(() => {
  const points: LinePoint[] = []
  const model = loadModel.value
  const baseTime = runStarted.value ?? startTime.value
  if (!Number.isFinite(baseTime)) return points
  const stages = Array.isArray(workload.value.stages) ? workload.value.stages as UnknownRecord[] : []
  const unit = Number(String(workload.value.time_unit || '1s') === '1m' ? 60 : 1)

  function add(time: number, value: number | null, label?: string): void {
    if (value == null || !Number.isFinite(value)) return
    points.push({ time, value })
  }

  if (model.includes('constant-vus')) {
    const vus = toFiniteNumber(workload.value.vus) || toFiniteNumber(props.report.load_goal?.target_vus)
    if (vus != null) add(baseTime, vus)
    return points
  }

  if (model.includes('arrival') || model.includes('ramping')) {
    if (model === 'constant-arrival-rate') {
      const configured = toFiniteNumber(workload.value.rate)
      const converted = configured == null ? null : configured / unit
      add(baseTime, converted)
      return points
    }

    if (stages.length) {
      let cumulativeSeconds = 0
      const startRate = toFiniteNumber(workload.value.start_rate)
      if (startRate != null) add(baseTime, startRate / unit)
      else {
        const firstTarget = toFiniteNumber(stages[0]?.target)
        if (firstTarget != null) add(baseTime, firstTarget / unit)
      }
      for (const stage of stages) {
        const seconds = Math.max(0, toFiniteNumber(stage.duration_seconds) || 0)
        cumulativeSeconds += seconds
        const target = toFiniteNumber(stage.target)
        if (target != null) add(baseTime + cumulativeSeconds, target / unit)
      }
      return points
    }

    if (model === 'ramping-vus') {
      const target = toFiniteNumber(workload.value.vus)
      if (target != null) add(baseTime, target)
    }
  }

  return points
})

const chartLines = computed<Record<string, LineSeries[]>>(() => {
  const actualWindowPoints = replayWindows.value.map(window => ({
    time: window.start,
    value: window.duration > 0
      ? (window.iterations > 0 ? window.iterations / window.duration : window.requests / window.duration)
      : 0,
  }))
  const p95Points = replayWindows.value
    .filter(window => window.p95 !== null)
    .map(window => ({ time: window.start, value: window.p95 as number }))
  const httpFailPoints = replayWindows.value
    .filter(window => window.requests > 0)
    .map(window => ({ time: window.start, value: window.http_failures / Math.max(1, window.requests) }))
  const businessFailPoints = replayWindows.value
    .filter(window => window.iterations > 0)
    .map(window => ({ time: window.start, value: window.business_failures / Math.max(1, window.iterations) }))

  const cpu = extractMonitoringLines(['cpu_percent', 'cpu_cores'])
  const mem = extractMonitoringLines(['memory_percent', 'memory_working_set_bytes'])

  return {
    pressure: [
      ...planPressureSeries.value.length
        ? [{ id: 'plan', name: '计划压力', color: '#0b6e9f', unit: planPressureSeries.value.some(item => item.value > 20) ? (loadModel.value.includes('vus') ? 'VU' : '次/秒') : '次/秒', points: planPressureSeries.value }]
        : [],
      { id: 'actual', name: '实际吞吐（近似）', color: '#0f766e', unit: '次/秒', points: actualWindowPoints },
    ],
    p95: [{ id: 'p95', name: 'P95', color: '#7c3aed', unit: 'ms', points: p95Points }],
    error: [
      { id: 'http', name: 'HTTP 错误率', color: '#dc2626', unit: '%', points: httpFailPoints },
      { id: 'business', name: '业务失败率', color: '#d97706', unit: '%', points: businessFailPoints },
    ],
    cpu,
    memory: mem,
  }
})

const lineRanges = computed<Record<string, { min: number; max: number }>>(() => {
  const toRange = (points: LinePoint[]): { min: number; max: number } => {
    const values = points.map(item => item.value).filter(Number.isFinite)
    if (!values.length) return { min: 0, max: 1 }
    const min = Math.min(...values)
    const max = Math.max(...values)
    if (min === max) return { min: Math.max(0, min * 0.95), max: Math.max(min * 1.2, 1) }
    return { min: min < 0 ? min : 0, max }
  }
  const ranges: Record<string, { min: number; max: number }> = {}
  for (const [key, lines] of Object.entries(chartLines.value)) {
    const merged = lines.flatMap(line => line.points.map(item => item.value))
    ranges[key] = toRange(merged.map(item => ({ value: item }) as LinePoint))
    for (const line of lines) {
      ranges[`${key}:${line.id}`] = toRange(line.points.map(item => ({ value: item.value }) as LinePoint))
    }
  }
  return ranges
})

const planToTime = computed(() => {
  const points = planPressureSeries.value
  if (!points.length) return null
  return (timestamp: number): number | null => {
    let value: number | null = null
    for (const item of points) {
      if (item.time <= timestamp) value = item.value
      else if (item.time > timestamp) break
    }
    return value
  }
})

const resourceFlags = computed(() => {
  const windows = replayWindows.value
  const cpuLines = chartLines.value.cpu
  const memLines = chartLines.value.memory
  return windows.map(window => {
    const cpuWindow = cpuLines.flatMap(line => line.points.filter(point => point.time >= window.start && point.time <= window.end).map(point => point.value)).filter(Number.isFinite)
    const memWindow = memLines.flatMap(line => line.points.filter(point => point.time >= window.start && point.time <= window.end).map(point => point.value)).filter(Number.isFinite)

    const cpuHigh = cpuWindow.some(value => value >= 85)
    const memoryHigh = memWindow.some(value => value >= 85)
    const cpuData = cpuWindow.length > 0
    const memoryData = memWindow.length > 0

    return {
      cpuHigh,
      cpuData,
      memHigh: memoryHigh,
      memData: memoryData,
    }
  })
})

const anomalyWindows = computed(() => {
  const threshold = p95Reference.value
  if (threshold == null || !replayWindows.value.length) return [] as boolean[]
  return replayWindows.value.map(window => window.p95 != null && window.p95 > threshold)
})

const anomalySegments = computed(() => {
  const mask = anomalyWindows.value
  const segments: Array<{ start: number; end: number }> = []
  let streak = 0
  for (let index = 0; index < mask.length; index++) {
    if (mask[index]) {
      streak += 1
      if (streak === 3) segments.push({ start: index - 2, end: index + 1 })
      else if (streak > 3 && segments.length) segments[segments.length - 1].end = index + 1
    } else streak = 0
  }
  return segments
})

const anomalyRecover = computed(() => {
  const first = anomalySegments.value[0]
  if (!first) return null
  const windows = replayWindows.value
  let streak = 0
  for (let index = first.end; index < windows.length; index++) {
    const inAnomaly = anomalyWindows.value[index]
    if (!inAnomaly) {
      streak += 1
      if (streak >= 3) return { start: index - 2, end: index + 1 }
      continue
    }
    streak = 0
  }
  return null
})

const replayEvents = computed<ReplayEvent[]>(() => {
  const events: ReplayEvent[] = []
  const start = startTime.value
  if (Number.isFinite(start)) {
    events.push({
      id: 'start',
      time: start,
      label: '开始',
      summary: '压测开始',
      details: ['运行开始，已建立时间基线。'],
      evidence: ['窗口起点由 series.started_at 与监控补充时间构成'],
    })
  }

  const stages = replayStages.value
  if (stages.length && Number.isFinite(start)) {
    for (const stage of stages) {
      const unit = loadModel.value.includes('vus') ? 'VU' : '次/秒'
      const startTargetText = stage.startPressure == null ? '未完整记录' : `${stage.startPressure} ${unit}`
      const targetText = stage.target == null ? '未完整记录' : `${stage.target} ${unit}`
      events.push({
        id: `stage-${stage.index}`,
        time: start + stage.start,
        label: `阶段 ${stage.index} 开始`,
        summary: `阶段 ${stage.index} 已定义，按时间窗口生效`,
        details: [
          `计划起始 ${startTargetText}`,
          `阶段目标 ${targetText}`,
          stage.duration ? `阶段时长 ${formatDuration(stage.duration)}` : '阶段时长未记录',
          stage.duration
            ? `阶段结束时刻 T+${formatDuration(stage.start + stage.duration)}`
            : '暂不支持精确阶段结束时刻，以上为当前可见定义。',
        ],
        evidence: ['来自 report.load_goal.stages / workload.stages'],
      })
    }

    for (let index = 1; index < stages.length; index++) {
      const previous = stages[index - 1]?.target || 0
      const current = stages[index]?.target || 0
      const previousTarget = Number(previous)
      const currentTarget = Number(current)
      const seconds = stages[index].start
      if (!Number.isFinite(seconds) || Number.isNaN(previousTarget) || Number.isNaN(currentTarget) || previousTarget <= currentTarget) continue
      events.push({
        id: `ramp-down-${index}`,
        time: start + seconds,
        label: `降压阶段（降幅 ${Math.max(0, previousTarget - currentTarget)}）`,
        summary: '压力轨迹出现降压意图',
        details: [`阶段目标从 ${previousTarget} ${loadModel.value.includes('vus') ? 'VU' : '次/秒'} 降到 ${currentTarget} ${loadModel.value.includes('vus') ? 'VU' : '次/秒'}。`, '该节点仅按配置阶段边界判断，不能自动推断降压已生效。'],
        evidence: ['来自 report.load_goal.stages 的 target 与阶段起点'],
      })
    }
  }

  const anomaly = anomalySegments.value[0]
  if (anomaly) {
    const startWindow = replayWindows.value[anomaly.start]
    const endWindow = replayWindows.value[anomaly.end - 1]
    const resource = resourceFlags.value
    const cpuHigh = anomaly.start < resource.length ? resource.slice(anomaly.start, anomaly.end).some(item => item.cpuHigh) : false
    const memHigh = anomaly.start < resource.length ? resource.slice(anomaly.start, anomaly.end).some(item => item.memHigh) : false
    const resourceMissing = anomaly.start < resource.length ? !resource.slice(anomaly.start, anomaly.end).some(item => item.cpuData || item.memData) : true

    events.push({
      id: 'anomaly-start',
      time: (endWindow?.start ?? startWindow?.start ?? startTime.value),
      label: '首次持续异常',
      summary: `P95 连续 ${anomaly.end - anomaly.start} 个窗口超过阈值`,
      details: [
        `运行中 P95 已连续偏离参考线（${formatNumber(p95Reference.value, 'ms')}）`,
        cpuHigh ? 'CPU 指标窗口内出现高值，存在资源争用候选' : (resourceMissing ? '当前窗口缺少 CPU/内存 95% 样本证据，不能断言瓶颈' : 'CPU 证据未见连续高值'),
        memHigh ? '内存指标窗口内出现高值，存在资源争用候选' : (resourceMissing ? '当前窗口缺少内存 95% 样本证据，不能断言瓶颈' : '内存证据未见连续高值'),
      ],
      evidence: ['根据 report.series 的窗口 P95 与监控采样窗口对齐'],
    })

    if (anomalyRecover.value) {
      const recoveryStart = replayWindows.value[anomalyRecover.value.start]
      const recoveryEnd = replayWindows.value[anomalyRecover.value.end - 1]
      events.push({
        id: 'recovery',
        time: recoveryStart?.start ?? (recoveryEnd?.start ?? startTime.value),
        label: '开始恢复观察',
        summary: '恢复窗口连续 3 个窗口回到阈值内',
        details: [
          `从 T+${formatDuration((recoveryStart?.start ?? 0) - (start || 0)} 起，P95 未持续超阈值 3 窗口`,
          `恢复覆盖区间至 ${formatDuration((recoveryEnd?.end ?? (recoveryEnd?.start ?? 0)) - (start || 0)}`,
        ],
        evidence: ['按窗口序列的连续 3 次未超阈值判断'],
      })
    } else if (props.run && ['finished', 'failed', 'cancelled'].includes(props.run.state)) {
      events.push({
        id: 'recovery-missed',
        time: runFinished.value || endTime.value,
        label: '恢复观察不足',
        summary: '异常后未观察到 3 个窗口持续恢复',
        details: [
          '当前仅能说明“未在阈值内稳定观察到恢复”，不能据此认定瓶颈已消除。',
          '如需继续结论，可补充更长恢复窗口再复测。',
        ],
        evidence: ['运行结束时点 / 现有窗口序列'],
      })
    }
  }

  const isTerminalState = typeof props.run?.state === 'string' && ['finished', 'failed', 'cancelled'].includes(props.run.state)
  if ((props.run?.finished_at && isTerminalState) || (runFinished.value && isTerminalState) || (isTerminalState && replayWindows.value.length)) {
    const finished = props.run?.finished_at ? parseTime(props.run.finished_at) : runFinished.value || endTime.value
    if (finished != null) {
      const state = props.run?.state || '未知'
      const reason = typeof props.run?.stop_reason === 'string' && props.run.stop_reason ? props.run.stop_reason : ''
      const stopLabel = state.includes('cancel') || reason.includes('cancel') || reason.includes('stop')
        ? '人工停止'
        : reason.includes('保护') || reason.includes('保护停止')
          ? '保护停止'
          : state === 'finished'
            ? '完成'
            : state
      events.push({
        id: 'finish',
        time: finished,
        label: stopLabel,
        summary: '执行进入结束状态',
        details: [
          `运行状态：${state}`,
          reason ? `停止说明：${reason}` : '未写入停止说明',
        ],
        evidence: ['由运行状态与 timestamps 生成'],
      })
    }
  }

  return events
    .filter(item => Number.isFinite(item.time))
    .filter((item, index, self) => self.findIndex(each => each.time === item.time && each.label === item.label) === index)
    .sort((left, right) => left.time - right.time)
})

const activeEvent = computed(() => {
  const time = cursorWindowTime.value
  if (!replayEvents.value.length) return null
  let current: ReplayEvent | null = null
  for (const item of replayEvents.value) {
    if (item.time <= time) current = item
    else break
  }
  return current || replayEvents.value[0] || null
})

const progressPercent = computed(() => {
  if (!replayWindows.value.length || !Number.isFinite(startTime.value) || !Number.isFinite(endTime.value) || endTime.value === startTime.value) return 0
  return Math.min(100, Math.max(0, ((cursorWindowTime.value - startTime.value) / timelineSpan.value) * 100))
})

function throughputInWindow(window: ReplayWindow | null): string {
  if (!window) return '—'
  if (!window.duration || !Number.isFinite(window.duration) || window.duration <= 0) return '—'
  const throughput = window.iterations > 0 ? window.iterations / window.duration : window.requests / window.duration
  return `${throughput.toLocaleString('zh-CN', { maximumFractionDigits: 2 })} 次/秒`
}

const currentPressureValue = computed(() => {
  const time = cursorWindowTime.value
  const actual = throughputInWindow(currentWindow.value)
  const planValue = planToTime.value?.(time)
  return [
    `计划 ${planValue == null ? '—' : `${planValue.toLocaleString('zh-CN')} ${loadModel.value.includes('vus') ? 'VU' : '次/秒'}`}`,
    `实际 ${actual}`,
  ]
})

const currentErrorText = computed(() => {
  const window = currentWindow.value
  const http = window && window.requests > 0 ? `${(window.http_failures / Math.max(1, window.requests) * 100).toLocaleString('zh-CN', { maximumFractionDigits: 2 })}%` : '—'
  const biz = window && window.iterations > 0 ? `${(window.business_failures / Math.max(1, window.iterations) * 100).toLocaleString('zh-CN', { maximumFractionDigits: 2 })}%` : '—'
  return `HTTP ${http} · 业务 ${biz}`
})

const currentLatencyText = computed(() => {
  const p95 = currentWindow.value?.p95
  return `P95 ${p95 == null ? '—' : `${p95.toLocaleString('zh-CN', { maximumFractionDigits: 2 })} ms`}`
})

const currentResourceText = computed(() => {
  const flags = resourceFlags.value[cursorWindowIndex.value]
  if (!flags) return '—'
  const cpu = chartLines.value.cpu
  const memory = chartLines.value.memory
  const cpuText = cpu.length ? valueAtCursor(cpu, cursorWindowTime.value) : 'CPU：—'
  const memText = memory.length ? valueAtCursor(memory, cursorWindowTime.value) : '内存：—'
  const missing = [] as string[]
  if (!flags.cpuData) missing.push('CPU 证据缺失')
  if (!flags.memData) missing.push('内存证据缺失')
  return `${cpuText} · ${memText}${missing.length ? ` · ${missing.join('；')}` : ''}`
})

function valueAtCursor(lines: LineSeries[], time: number): string {
  const entries = lines
    .map(line => {
      if (!line.points.length) return null
      let value: number | null = null
      for (const item of line.points) {
        if (item.time <= time) value = item.value
        else break
      }
      if (value == null) return null
      if (line.unit === '%') return `${line.name} ${formatRate(value / 100)} (${line.unit})`
      return `${line.name} ${formatNumber(value, line.unit)}`
    })
    .filter(Boolean)
  return entries.length ? entries.join(' · ') : '—'
}

function extractMonitoringLines(metricFilter: string[]): LineSeries[] {
  const monitoring = toRecord(props.report.monitoring)
  const services = Array.isArray(monitoring.services) ? monitoring.services : []
  const metrics: LineSeries[] = []
  const palette = ['#0ea5e9', '#2563eb', '#16a34a', '#db2777', '#a21caf']
  let index = 0

  for (const service of services as UnknownRecord[]) {
    const serviceName = String(service.name || service.revision_id || '未命名服务')
    const serviceMetrics = Array.isArray(service.metrics) ? service.metrics : []

    for (const metric of serviceMetrics as UnknownRecord[]) {
      const key = String(metric.key || '').trim()
      if (!metricFilter.includes(key)) continue
      const labels = metric.labels && typeof metric.labels === 'object' ? metric.labels as UnknownRecord : undefined
      const meta = Object.entries(labels || {}).filter(([label]) => label !== '__name__').map(([label, value]) => `${label}=${value}`).join(' / ')
      const rawPoints = Array.isArray(metric.points) ? metric.points : []
      const points = rawPoints
        .map((item: UnknownRecord): LinePoint | null => {
          const time = toFiniteNumber(item.timestamp)
          if (!Number.isFinite(time)) return null
          const value = toFiniteNumber(item.value)
          if (value == null || !Number.isFinite(value)) return null
          if (key === 'memory_working_set_bytes') return { time, value: value / 1048576 }
          return { time, value }
        })
        .filter((point): point is LinePoint => point != null && point.time >= startTime.value - 0.5 && point.time <= endTime.value + 0.5)
        .sort((left, right) => left.time - right.time)

      if (!points.length) continue

      const unit = key === 'memory_working_set_bytes' ? 'MiB' : key.includes('percent') || key === 'memory_percent' || key === 'cpu_percent' ? '%' : 'cores'
      const name = `${serviceName}${meta ? `（${meta}）` : ''} · ${metric.label || key}`
      metrics.push({
        id: `${service.revision_id || service.id || serviceName}-${metric.key}-${index++}`,
        name,
        color: palette[index % palette.length],
        unit,
        points,
      })
    }
  }

  return metrics
}

const xAxis = (timestamp: number) => ((timestamp - startTime.value) / timelineSpan.value) * 100

function yAxis(value: number, min: number, max: number): number {
  const safeMax = Math.max(1, max)
  const safeMin = min
  const normalized = (value - safeMin) / (safeMax - safeMin)
  return 96 - normalized * 86
}

function lineSegmentsFor(chartKey: string, line: LineSeries): string[] {
  const merged = line.points
    .filter(point => point.time >= startTime.value && point.time <= endTime.value)
    .filter(point => Number.isFinite(point.value))
  if (!merged.length) return []
  const gap = inferWindowGap(replayWindows.value) * 2
  const segments: string[] = []
  let current: string[] = []

  const range = lineRanges.value[`${chartKey}:${line.id}`] ?? lineRanges.value[chartKey] ?? { min: 0, max: 1 }

  for (let index = 0; index < merged.length; index++) {
    const point = merged[index]
    const previousPoint = index > 0 ? merged[index - 1] : null
    if (previousPoint && point.time - previousPoint.time > gap) {
      if (current.length) segments.push(current.join(' '))
      current = []
    }
    current.push(`${xAxis(point.time)},${yAxis(point.value, range.min, range.max)}`)
  }
  if (current.length) segments.push(current.join(' '))
  return segments
}

const chartStateText = computed(() => {
  const window = currentWindow.value
  if (!window) return '尚未有窗口采样。'
  const hasP95 = window.p95 != null
  const highP95 = hasP95 && p95Reference.value != null && window.p95 != null && window.p95 > p95Reference.value
  const throughputValue = window.duration > 0
    ? window.iterations > 0 ? window.iterations / window.duration : window.requests / window.duration
    : null
  const facts: string[] = []
  facts.push(`窗口请求 ${window.requests}；吞吐 ${throughputValue == null ? '—' : `${throughputValue.toLocaleString('zh-CN', { maximumFractionDigits: 2 })} 次/秒`}`)
  facts.push(`HTTP 失败率 ${window.requests > 0 ? ((window.http_failures / window.requests) * 100).toLocaleString('zh-CN', { maximumFractionDigits: 2 }) : 0}%`)
  facts.push(`业务失败率 ${window.iterations > 0 ? ((window.business_failures / window.iterations) * 100).toLocaleString('zh-CN', { maximumFractionDigits: 2 }) : 0}%`)
  if (hasP95) facts.push(`P95 ${window.p95?.toLocaleString('zh-CN', { maximumFractionDigits: 2 })} ms`)
  if (p95Reference.value != null) facts.push(`参考线 ${p95Reference.value.toLocaleString('zh-CN', { maximumFractionDigits: 2 })} ms，当前${highP95 ? '偏高' : '未偏离'}。`)
  return facts.join('；')
})

function tick(): void {
  const max = Math.max(0, replayWindows.value.length - 1)
  if (!playing.value) return
  if (!max) {
    stop()
    return
  }
  const step = reducedMotion.value ? 1 : speed.value * 0.2
  const next = cursor.value + step
  if (next >= max) {
    cursor.value = max
    stop()
    return
  }
  cursor.value = next
}
function play(): void {
  if (!replayWindows.value.length) return
  if (playing.value) return
  playing.value = true
  if (timer.value) clearInterval(timer.value)
  const interval = reducedMotion.value ? 500 : 250
  timer.value = setInterval(() => {
    tick()
  }, interval)
}
function stop(): void {
  playing.value = false
  if (timer.value) {
    clearInterval(timer.value)
    timer.value = null
  }
}
function reset(): void {
  stop()
  cursor.value = 0
}
function seekTo(time: number): void {
  stop()
  cursor.value = clampWindowIndexByTime(time)
}
function moveWindow(steps: number): void {
  stop()
  const max = Math.max(0, replayWindows.value.length - 1)
  cursor.value = Math.min(max, Math.max(0, cursor.value + steps))
}
function clampWindowIndexByTime(time: number): number {
  const windows = replayWindows.value
  if (!windows.length) return 0
  for (let index = 0; index < windows.length; index++) {
    const window = windows[index]
    if (time < window.start) return index
    if (time >= window.start && time < window.end) return index
  }
  return windows.length - 1
}
function setSpeed(next: number): void { stop(); speed.value = next }
function jumpToStart(): void { seekTo(startTime.value) }
function jumpToFirstAnomaly(): void {
  const first = anomalySegments.value[0]
  if (!first) return
  const target = replayWindows.value[first.start]?.start
  if (target != null) seekTo(target)
}
function jumpToRecovery(): void {
  const point = anomalyRecover.value
  const recovery = point ? replayWindows.value[point.start]?.start : null
  if (recovery != null) seekTo(recovery)
}
function jumpToEvent(event: ReplayEvent): void { seekTo(event.time) }

function handleReplayKeydown(event: KeyboardEvent): void {
  const target = event.target as HTMLElement | null
  if (!event.key || (target && /^(INPUT|TEXTAREA|SELECT|OPTION|BUTTON|A)$/.test(target.tagName))) return
  if (event.code === 'Space') {
    event.preventDefault()
    if (playing.value) stop()
    else play()
    return
  }
  if (event.key === 'ArrowLeft') {
    event.preventDefault()
    moveWindow(-1)
    return
  }
  if (event.key === 'ArrowRight') {
    event.preventDefault()
    moveWindow(1)
    return
  }
  if (event.key === 'Home') {
    event.preventDefault()
    jumpToStart()
    return
  }
  if (event.key === 'End') {
    event.preventDefault()
    seekTo(endTime.value)
    return
  }
  if (event.key === '1') {
    setSpeed(1)
    return
  }
  if (event.key === '2') {
    setSpeed(2)
    return
  }
  if (event.key === '5') {
    setSpeed(5)
    return
  }
  if (event.key === '0') {
    setSpeed(10)
  }
}

function setupMotionPreference(): void {
  if (typeof window === 'undefined' || typeof window.matchMedia !== 'function') return
  mediaQuery.value = window.matchMedia('(prefers-reduced-motion: reduce)')
  reducedMotion.value = mediaQuery.value.matches
  if (mediaQuery.value.addEventListener) {
    mediaQuery.value.addEventListener('change', reducedMotionChangeHandler)
  } else {
    mediaQuery.value.addListener(reducedMotionChangeHandler)
  }
}

function cleanupMotionPreference(): void {
  const query = mediaQuery.value
  if (!query || (!query.removeEventListener && !query.removeListener)) return
  if (query.removeEventListener) {
    query.removeEventListener('change', reducedMotionChangeHandler)
  } else {
    query.removeListener(reducedMotionChangeHandler)
  }
}

function formatEventLabel(value: number): string { return `T+${formatDuration(Math.max(0, value - startTime.value))}` }

watch(() => props.report, () => { reset() })
onMounted(() => {
  setupMotionPreference()
  window.addEventListener('keydown', handleReplayKeydown)
})
onBeforeUnmount(() => {
  stop()
  cleanupMotionPreference()
  window.removeEventListener('keydown', handleReplayKeydown)
})

const visibleEvents = computed(() => replayEvents.value.filter(event => Number.isFinite(event.time)).slice(0, 10))
</script>

<template>
  <section class="load-run-replay" aria-label="压测过程回放">
    <header>
      <div>
        <h2>压测过程回放（仅使用已存储证据）</h2>
        <p>时间轴按真实采样窗口同步推进。播放不发起任何新请求。</p>
        <p class="replay-hint">{{ keyboardHint }}</p>
      </div>
      <small>{{ available ? `已有 ${replayWindows.length} 个窗口` : '暂无可回放窗口' }}</small>
    </header>

    <section v-if="!available" class="state-message state-warning">该报告缺少可用于回放的窗口时序数据，建议先补全报告采样。</section>

    <template v-else>
      <div class="replay-controls">
        <div class="replay-buttons">
          <button type="button" class="secondary-command" @click="jumpToStart">跳到开始</button>
          <button type="button" class="secondary-command" @click="jumpToFirstAnomaly">跳到首次异常</button>
          <button type="button" class="secondary-command" @click="jumpToRecovery">跳到恢复观察</button>
          <button class="secondary-command" type="button" @click="play">{{ playing ? '暂停中' : '播放' }}</button>
          <button class="secondary-command" type="button" @click="stop">停止</button>
          <button class="secondary-command" type="button" @click="reset">重播</button>
        </div>
        <div class="replay-speed">
          <label><span>倍速</span>
            <select :value="speed" @change="setSpeed(Number(($event.target as HTMLSelectElement).value))">
              <option :value="1">1x</option>
              <option :value="2">2x</option>
              <option :value="5">5x</option>
              <option :value="10">10x</option>
            </select>
          </label>
        </div>
      </div>

      <div class="replay-time">
        <span>{{ cursorClockText }}</span>
        <span>{{ cursorOffsetText }}</span>
        <span>{{ progressPercent.toFixed(1) }}%</span>
      </div>
      <input class="replay-slider" type="range" :min="0" :max="Math.max(replayWindows.length - 1, 0)" :step="0.1" v-model.number="cursor" @input="stop" />

      <section class="replay-readout">
        <article>
          <h3>当前窗口说明</h3>
          <p>{{ chartStateText }}</p>
          <p>{{ currentPressureValue.join(' | ') }}</p>
          <p>{{ currentLatencyText }}</p>
          <p>{{ currentErrorText }}</p>
          <p>{{ currentResourceText }}</p>
        </article>
      </section>

      <section class="replay-grid">
        <article>
          <h3>压力曲线（计划 & 实际）</h3>
          <div class="replay-chart" role="img" aria-label="压力曲线">
            <div class="chart-axis" aria-hidden="true">{{ loadModel.includes('arrival') ? '单位：次/秒' : loadModel.includes('vus') ? '单位：VU（计划）/ 次/秒（实际）' : '单位：次/秒' }}</div>
            <svg viewBox="0 0 100 100" preserveAspectRatio="none">
              <line x1="2" x2="98" y1="96" y2="96" stroke="#cbd5e1" />
              <line x1="2" x2="2" y1="10" y2="96" stroke="#cbd5e1" />
              <template v-for="line in chartLines.pressure" :key="line.id">
                <template v-for="segment in lineSegmentsFor('pressure', line)" :key="`${line.id}-${segment}`">
                  <polyline :points="segment" :stroke="line.color" fill="none" stroke-width="1.4" vector-effect="non-scaling-stroke" />
                </template>
                <circle
                  v-for="point in line.points.filter(item => item.time <= cursorWindowTime && item.time >= startTime)"
                  :key="`p-${line.id}-${point.time}`"
                  :cx="xAxis(point.time)"
                  :cy="yAxis(point.value, lineRanges.pressure?.min || 0, lineRanges.pressure?.max || 1)"
                  r="1.3"
                  :fill="line.color"
                  stroke="white"
                  stroke-width="0.5">
                  <title>{{ line.name }}：{{ formatNumber(point.value, line.unit) }}</title>
                </circle>
              </template>
              <line
                :x1="xAxis(cursorWindowTime)"
                y1="8"
                y2="96"
                stroke="rgba(37,99,235,0.6)"
                stroke-width="1"
                stroke-dasharray="4 3" />
            </svg>
          </div>
          <p class="replay-plot-legend">{{ currentPressureValue.join(' ｜ ') }}</p>
        </article>

        <article>
          <h3>P95 随时间变化</h3>
          <div class="replay-chart" role="img" aria-label="P95 曲线">
            <div class="chart-axis" aria-hidden="true">单位：ms</div>
            <svg viewBox="0 0 100 100" preserveAspectRatio="none">
              <line x1="2" x2="98" y1="96" y2="96" stroke="#cbd5e1" />
              <line x1="2" x2="2" y1="10" y2="96" stroke="#cbd5e1" />
              <template v-for="line in chartLines.p95" :key="line.id">
                <template v-for="segment in lineSegmentsFor('p95', line)" :key="`${line.id}-${segment}`">
                  <polyline :points="segment" :stroke="line.color" fill="none" stroke-width="1.5" vector-effect="non-scaling-stroke" />
                </template>
                <circle
                  v-for="point in line.points.filter(item => item.time <= cursorWindowTime && item.time >= startTime)"
                  :key="`p-${line.id}-${point.time}`"
                  :cx="xAxis(point.time)"
                  :cy="yAxis(point.value, lineRanges.p95?.min || 0, lineRanges.p95?.max || 1)"
                  r="1.3"
                  fill="#7c3aed"
                  stroke="white"
                  stroke-width="0.5">
                  <title>{{ formatNumber(point.value, 'ms') }}</title>
                </circle>
              </template>
              <line :x1="xAxis(cursorWindowTime)" y1="8" y2="96" stroke="rgba(37,99,235,0.6)" stroke-width="1" stroke-dasharray="4 3" />
            </svg>
          </div>
          <p class="replay-plot-legend">{{ currentLatencyText }}<span v-if="p95Reference != null"> · 参考线 {{ formatNumber(p95Reference, 'ms') }}</span></p>
        </article>

        <article>
          <h3>错误率（HTTP / 业务）</h3>
          <div class="replay-chart" role="img" aria-label="错误率曲线">
            <div class="chart-axis" aria-hidden="true">单位：%</div>
            <svg viewBox="0 0 100 100" preserveAspectRatio="none">
              <line x1="2" x2="98" y1="96" y2="96" stroke="#cbd5e1" />
              <line x1="2" x2="2" y1="10" y2="96" stroke="#cbd5e1" />
              <template v-for="line in chartLines.error" :key="line.id">
                <template v-for="segment in lineSegmentsFor('error', line)" :key="`${line.id}-${segment}`">
                  <polyline :points="segment" :stroke="line.color" fill="none" stroke-width="1.4" vector-effect="non-scaling-stroke" />
                </template>
              </template>
              <line :x1="xAxis(cursorWindowTime)" y1="8" y2="96" stroke="rgba(37,99,235,0.6)" stroke-width="1" stroke-dasharray="4 3" />
            </svg>
          </div>
          <p class="replay-plot-legend">{{ currentErrorText }}</p>
        </article>

        <article>
          <h3>CPU 证据曲线（%）</h3>
          <div class="replay-chart" role="img" aria-label="CPU 证据曲线">
            <div class="chart-axis" aria-hidden="true">单位：%</div>
            <svg viewBox="0 0 100 100" preserveAspectRatio="none">
              <line x1="2" x2="98" y1="96" y2="96" stroke="#cbd5e1" />
              <line x1="2" x2="2" y1="10" y2="96" stroke="#cbd5e1" />
              <template v-for="line in chartLines.cpu" :key="line.id">
                <template v-for="segment in lineSegmentsFor('cpu', line)" :key="`${line.id}-${segment}`">
                  <polyline :points="segment" :stroke="line.color" fill="none" stroke-width="1.3" vector-effect="non-scaling-stroke" />
                </template>
              </template>
              <line :x1="xAxis(cursorWindowTime)" y1="8" y2="96" stroke="rgba(37,99,235,0.6)" stroke-width="1" stroke-dasharray="4 3" />
            </svg>
          </div>
          <p class="replay-plot-legend">{{ valueAtCursor(chartLines.cpu, cursorWindowTime) }}</p>
        </article>

        <article>
          <h3>内存 证据曲线（MiB / %）</h3>
          <div class="replay-chart" role="img" aria-label="内存证据曲线">
            <div class="chart-axis" aria-hidden="true">单位：多指标可混用</div>
            <svg viewBox="0 0 100 100" preserveAspectRatio="none">
              <line x1="2" x2="98" y1="96" y2="96" stroke="#cbd5e1" />
              <line x1="2" x2="2" y1="10" y2="96" stroke="#cbd5e1" />
              <template v-for="line in chartLines.memory" :key="line.id">
                <template v-for="segment in lineSegmentsFor('memory', line)" :key="`${line.id}-${segment}`">
                  <polyline :points="segment" :stroke="line.color" fill="none" stroke-width="1.3" vector-effect="non-scaling-stroke" />
                </template>
              </template>
              <line :x1="xAxis(cursorWindowTime)" y1="8" y2="96" stroke="rgba(37,99,235,0.6)" stroke-width="1" stroke-dasharray="4 3" />
            </svg>
          </div>
          <p class="replay-plot-legend">{{ valueAtCursor(chartLines.memory, cursorWindowTime) }}</p>
        </article>
      </section>

      <section class="replay-events" v-if="visibleEvents.length">
        <header><h3>关键事件说明</h3><p>当前仅展示有证据或已确认事件时刻。</p></header>
        <div class="replay-event-tags">
          <button v-for="event in visibleEvents" :key="event.id" type="button" class="event-tag" @click="jumpToEvent(event)">{{ formatEventLabel(event.time) }} · {{ event.label }}</button>
        </div>
        <article v-if="activeEvent" class="replay-event-card">
          <strong>{{ activeEvent.label }}（{{ formatEventLabel(activeEvent.time) }}）</strong>
          <p>{{ activeEvent.summary }}</p>
          <ul>
            <li v-for="item in activeEvent.details" :key="item">{{ item }}</li>
          </ul>
          <details>
            <summary>证据与限制</summary>
            <ul><li v-for="item in activeEvent.evidence" :key="item">{{ item }}</li></ul>
          </details>
        </article>
        <p class="event-note">AI 仅负责解释已出现证据，不会在窗口中新增未观测时间点；缺失窗口保持未采样，不会插值。</p>
      </section>
    </template>
  </section>
</template>

<style scoped>
.load-run-replay{padding:18px;border:1px solid #d8e3ed;border-radius:12px;background:#fff;color:#0f172a;margin:16px 0;}
.load-run-replay h2{margin:0;font-size:20px}.load-run-replay header{display:flex;justify-content:space-between;gap:12px;align-items:flex-start}.load-run-replay p{font-size:13px;line-height:1.6;color:#52627e}
.replay-hint{font-size:12px;color:#64748b;margin-top:6px}
.replay-controls{display:flex;gap:10px;flex-wrap:wrap;align-items:center;justify-content:space-between}
.replay-buttons{display:flex;gap:8px;flex-wrap:wrap}.replay-speed label{display:grid;gap:4px;font-size:12px;color:#475569}.replay-speed select{height:33px;min-width:74px}
.replay-time{display:flex;justify-content:space-between;font-size:13px;color:#475569;margin:12px 0}.replay-slider{width:100%;accent-color:#0ea5e9}
.replay-readout{margin:10px 0;padding:10px 12px;border:1px solid #dce6f3;border-radius:8px;background:#f8fbff}
.replay-readout article{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:8px}.replay-readout h3{margin:0 0 4px}.replay-readout p{margin:0;color:#1e293b;font-size:13px}
.replay-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:12px;margin-top:12px}.replay-grid>article{padding:12px;border:1px solid #dce6ef;border-radius:10px;background:#fbfdff}
.replay-grid h3{margin:0 0 8px;font-size:15px}.chart-axis{font-size:11px;color:#64748b;margin-bottom:4px}.replay-chart{border:1px solid #e5ecf3;border-radius:6px;padding:8px;background:#fff}
.replay-chart svg{width:100%;height:190px}.replay-chart polyline,.replay-chart line{vector-effect:non-scaling-stroke}
.replay-plot-legend{margin:8px 0 0;font-size:12px;color:#475569}.replay-events{margin-top:14px;padding:10px;border:1px solid #d9e2ef;border-radius:10px;background:#f8fcff}.replay-events h3{margin:0 0 8px}.replay-events .event-note{margin:6px 0 0;color:#64748b;font-size:12px}
.replay-event-tags{display:flex;flex-wrap:wrap;gap:8px;margin-bottom:8px}.event-tag{font-size:12px;border:1px solid #bfdbfe;background:#f0f9ff;color:#0f4c81;padding:6px 9px;border-radius:999px;white-space:nowrap}
.replay-event-card{padding:10px;border:1px solid #dbe8f6;border-radius:8px;background:white}.replay-event-card strong{display:block;margin-bottom:4px}.replay-event-card p{margin:0 0 6px}.replay-event-card ul{margin:0;padding-left:18px}.replay-event-card summary{cursor:pointer;color:#0f4c81;font-size:13px}
@media (max-width: 900px){.replay-grid{grid-template-columns:1fr}.replay-readout article{grid-template-columns:1fr}.load-run-replay{padding:14px}}
</style>
