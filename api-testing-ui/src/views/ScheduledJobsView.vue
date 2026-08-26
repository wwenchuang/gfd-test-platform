<script setup lang="ts">
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { CalendarClock, Check, ChevronDown, ChevronRight, ExternalLink, Pencil, Play, RefreshCw, Save, Trash2 } from 'lucide-vue-next'

import type { ApiBaselineCase, ApiTestTask, CaseVersion, ScheduledJob } from '../api/contracts'
import { baselineGroup, useBaselinesStore } from '../stores/baselines'
import { useCasesStore } from '../stores/cases'
import { useContextStore } from '../stores/context'
import { type ScheduledJobInput, useScheduledJobsStore } from '../stores/scheduledJobs'
import { useTasksStore } from '../stores/tasks'
import { confirmApiExecution } from '../utils/executionConfirmation'
import { formatPassRate, statusLabel } from '../utils/executionPresentation'

const context = useContextStore()
const baselines = useBaselinesStore()
const cases = useCasesStore()
const scheduledJobs = useScheduledJobsStore()
const tasks = useTasksStore()
const router = useRouter()

const form = reactive({
  name: '',
  targetType: 'baseline_group' as ScheduledJob['target_type'],
  scheduleType: 'daily' as ScheduledJob['schedule_type'],
  cronExpression: '',
  environmentStrategy: 'fixed_revision' as ScheduledJob['environment_strategy'],
  enabled: true,
  notifyFeishu: false,
  retryCount: 0,
  timeoutSeconds: 1800,
})
const selectedTargetIds = ref<string[]>([])
const targetSearch = ref('')
const targetLoadError = ref('')
const editingJobId = ref('')
const expandedTargetGroups = ref<Set<string>>(new Set())
let suspendTargetReset = false

interface TargetOption {
  id: string
  title: string
  subtitle: string
  meta: string
}

interface CronValidation {
  valid: boolean
  message: string
}

const scheduleOptions: Array<{ value: ScheduledJob['schedule_type']; label: string }> = [
  { value: 'daily', label: '每天' },
  { value: 'weekly', label: '每周' },
  { value: 'cron', label: 'Cron' },
]
const cronPresets = [
  { id: 'daily', title: '每天 02:00', expression: '0 2 * * *', description: '夜间稳定回归' },
  { id: 'weekday', title: '工作日 09:00', expression: '0 9 * * 1-5', description: '工作日前巡检' },
  { id: 'weekly', title: '每周一 09:00', expression: '0 9 * * 1', description: '周回归入口' },
  { id: 'monthly', title: '每月 1 日 10:00', expression: '0 10 1 * *', description: '月度基线检查' },
  { id: 'half-hour', title: '每 30 分钟', expression: '*/30 * * * *', description: '高频环境探活' },
]
const scheduleDefaultExpressions: Record<ScheduledJob['schedule_type'], string> = {
  daily: '0 2 * * *',
  weekly: '0 9 * * 1',
  cron: '',
}
const scheduleTimeDescriptions: Record<ScheduledJob['schedule_type'], string> = {
  daily: '每天 02:00 执行',
  weekly: '每周一 09:00 执行',
  cron: '按 Cron 表达式执行',
}

const projectId = computed(() => context.projectId || context.projects[0]?.id || '')
const sourceRevisionId = computed(() => context.sourceRevisionId || context.sourceRevisions.find(item => item.project_id === projectId.value)?.id || '')
const environmentRevisionId = computed(() => context.environmentRevisionId || context.environmentRevisions.find(item => item.project_id === projectId.value)?.id || '')
const environmentId = computed(() => context.environmentRevisions.find(item => item.id === environmentRevisionId.value)?.environment_id || '')
const targetPickerTitle = computed(() => ({
  cases: '选择已保存用例',
  task: '选择已保存任务',
  baselines: '选择基线',
  baseline_group: '选择基线分组',
}[form.targetType]))
const targetPickerSummary = computed(() => selectedTargetIds.value.length
  ? `已选 ${selectedTargetIds.value.length} 项`
  : form.targetType === 'task' ? '单选任务' : '可多选')
const targetSearchPlaceholder = computed(() => ({
  cases: '搜索用例名称、请求路径',
  task: '搜索任务名称',
  baselines: '搜索基线名称、路径、分组',
  baseline_group: '搜索分组名称',
}[form.targetType]))
const availableBaselines = computed(() => baselines.items.filter(item => item.status !== 'archived'))
const targetOptions = computed<TargetOption[]>(() => {
  if (form.targetType === 'baseline_group') return baselineGroupOptions()
  if (form.targetType === 'baselines') return availableBaselines.value.map(baselineOption)
  if (form.targetType === 'cases') return Object.values(cases.versions).map(caseOption).sort(byTitle)
  return tasks.tasks.map(taskOption).sort(byTitle)
})
const filteredTargetOptions = computed(() => {
  const keyword = targetSearch.value.trim().toLocaleLowerCase()
  if (!keyword) return targetOptions.value
  return targetOptions.value.filter(option => [option.title, option.subtitle, option.meta]
    .join(' ')
    .toLocaleLowerCase()
    .includes(keyword))
})
const filteredBaselineGroups = computed(() => {
  const groups = new Map<string, TargetOption[]>()
  for (const option of filteredTargetOptions.value) {
    const name = option.meta || '未分组'
    groups.set(name, [...(groups.get(name) || []), option])
  }
  return [...groups.entries()]
    .sort(([left], [right]) => left.localeCompare(right, 'zh-CN'))
    .map(([name, options]) => ({ name, options }))
})
const editorTitle = computed(() => editingJobId.value ? '编辑定时任务' : '新建定时任务')
const saveLabel = computed(() => {
  if (scheduledJobs.saving) return '保存中'
  return editingJobId.value ? '保存修改' : '保存定时任务'
})
const cronValidation = computed(() => describeCronExpression(form.cronExpression))
const scheduleDescription = computed(() => {
  if (form.scheduleType === 'cron') return cronValidation.value.valid ? cronValidation.value.message : '按 Cron 表达式执行'
  return scheduleTimeDescriptions[form.scheduleType]
})

onMounted(async () => {
  await Promise.all([context.loadSavedContext(), context.loadOptions()])
  await Promise.all([
    projectId.value ? scheduledJobs.load(projectId.value) : Promise.resolve(),
    loadTargetAssets(),
  ])
})

watch(() => form.targetType, () => {
  if (suspendTargetReset) return
  selectedTargetIds.value = []
  targetSearch.value = ''
  expandedTargetGroups.value = new Set()
})

function targetIds(): string[] {
  return [...selectedTargetIds.value]
}

async function saveJob(): Promise<void> {
  if (!projectId.value || !sourceRevisionId.value || !environmentRevisionId.value) {
    scheduledJobs.error = '请先选择项目、接口版本和执行环境'
    return
  }
  const ids = targetIds()
  if (!ids.length) {
    scheduledJobs.error = `请先${targetPickerTitle.value}`
    return
  }
  if (form.scheduleType === 'cron' && !cronValidation.value.valid) {
    scheduledJobs.error = cronValidation.value.message
    return
  }
  const input = buildJobInput(ids)
  if (editingJobId.value) {
    await scheduledJobs.update(editingJobId.value, input)
  } else {
    await scheduledJobs.create(input)
  }
  resetEditor()
}

function buildJobInput(ids: string[]): ScheduledJobInput {
  return {
    project_id: projectId.value,
    source_revision_id: sourceRevisionId.value,
    environment_revision_id: environmentRevisionId.value,
    environment_id: form.environmentStrategy === 'latest_environment' ? environmentId.value : undefined,
    name: form.name.trim(),
    target_type: form.targetType,
    target_ids: ids,
    schedule_type: form.scheduleType,
    cron_expression: scheduleExpression(),
    environment_strategy: form.environmentStrategy,
    enabled: form.enabled,
    notify_feishu: form.notifyFeishu,
    retry_count: form.retryCount,
    timeout_seconds: form.timeoutSeconds,
  }
}

async function runJob(job: ScheduledJob): Promise<void> {
  const revisionId = job.environment_strategy === 'latest_environment'
    ? context.environmentRevisions.find(item => item.environment_id === job.environment_id)?.id
    : job.environment_revision_id
  const environmentName = context.environmentRevisions.find(item => item.id === revisionId)?.name || '任务配置环境'
  if (!confirmApiExecution({
    action: '手动执行定时任务',
    environmentName,
    targetName: job.name,
    caseCount: job.target_ids.length,
  })) return
  const execution = await scheduledJobs.runOnce(job.id)
  await router.push({ name: 'runs', query: { executionId: execution.id } })
}

async function loadTargetAssets(): Promise<void> {
  if (!projectId.value) return
  targetLoadError.value = ''
  const results = await Promise.allSettled([
    baselines.load({ projectId: projectId.value, sourceRevisionId: sourceRevisionId.value, environmentRevisionId: environmentRevisionId.value }),
    tasks.list(projectId.value),
    sourceRevisionId.value ? cases.loadSavedCases(sourceRevisionId.value) : Promise.resolve(),
  ])
  const rejected = results.find(item => item.status === 'rejected')
  if (rejected?.status === 'rejected') {
    targetLoadError.value = rejected.reason instanceof Error ? rejected.reason.message : '无法读取可选目标'
  }
}

async function refreshAll(): Promise<void> {
  await Promise.all([
    projectId.value ? scheduledJobs.load(projectId.value) : Promise.resolve(),
    loadTargetAssets(),
  ])
}

function setScheduleType(type: ScheduledJob['schedule_type']): void {
  form.scheduleType = type
  if (type === 'cron' && !form.cronExpression.trim()) applyCronPreset(cronPresets[0].expression)
}

function applyCronPreset(expression: string): void {
  form.cronExpression = expression
}

function scheduleExpression(): string {
  if (form.scheduleType === 'cron') return form.cronExpression.trim()
  return scheduleDefaultExpressions[form.scheduleType]
}

function toggleTarget(id: string): void {
  if (form.targetType === 'task') {
    selectedTargetIds.value = selectedTargetIds.value[0] === id ? [] : [id]
    return
  }
  selectedTargetIds.value = selectedTargetIds.value.includes(id)
    ? selectedTargetIds.value.filter(item => item !== id)
    : [...selectedTargetIds.value, id]
}

function isTargetSelected(id: string): boolean {
  return selectedTargetIds.value.includes(id)
}

function targetTypeLabel(type: ScheduledJob['target_type']): string {
  return {
    cases: '多个用例',
    task: '已保存任务',
    baselines: '多条基线',
    baseline_group: '基线分组',
  }[type]
}

function targetGroupExpanded(name: string): boolean {
  return Boolean(targetSearch.value.trim()) || expandedTargetGroups.value.has(name)
}

function toggleTargetGroup(name: string): void {
  const next = new Set(expandedTargetGroups.value)
  if (next.has(name)) next.delete(name)
  else next.add(name)
  expandedTargetGroups.value = next
}

function jobTargetSummary(job: ScheduledJob): string {
  if (job.target_type === 'baseline_group') return job.target_ids.join('、') || '暂无目标'
  const optionMap = new Map(targetOptionsForType(job.target_type).map(option => [option.id, option]))
  const resolved = job.target_ids.map(id => optionMap.get(id)).filter((item): item is TargetOption => Boolean(item))
  if (!resolved.length) return `已选 ${job.target_ids.length} 项，目标详情待加载`
  const labels = resolved.slice(0, 2).map(option => `${option.title}${option.meta ? ` · ${option.meta}` : ''}`)
  const remaining = job.target_ids.length - labels.length
  return `${labels.join('；')}${remaining > 0 ? `；另 ${remaining} 项` : ''}`
}

function targetOptionsForType(type: ScheduledJob['target_type']): TargetOption[] {
  if (type === 'baselines') return availableBaselines.value.map(baselineOption)
  if (type === 'cases') return Object.values(cases.versions).map(caseOption)
  if (type === 'task') return tasks.tasks.map(taskOption)
  return baselineGroupOptions()
}

function scheduleLabel(job: ScheduledJob): string {
  const expression = job.effective_cron_expression || job.cron_expression || scheduleDefaultExpressions[job.schedule_type]
  const description = describeCronExpression(expression)
  return description.valid ? description.message : expression || '未配置周期'
}

function scheduleExecutionSummary(job: ScheduledJob): string {
  const summary = job.latest_execution_summary || {}
  const total = numberValue(summary.total)
  const passed = numberValue(summary.passed)
  if (!job.latest_execution_id) return '尚未触发'
  if (!total) return statusLabel(job.latest_execution_state || 'QUEUED')
  return `通过 ${passed}/${total} · ${formatPassRate(passed, total)}`
}

function triggerLabel(trigger: string | null): string {
  return trigger === 'schedule' || trigger === 'scheduler' ? '调度' : trigger === 'manual' ? '手动' : '触发'
}

function formatDateTime(value: string | null, utcOffset: string): string {
  if (!value) return '暂无'
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return '未知时间'
  const match = /^([+-])(\d{2}):(\d{2})$/.exec(utcOffset)
  if (!match) return date.toLocaleString('zh-CN', { hour12: false })
  const direction = match[1] === '-' ? -1 : 1
  const offsetMinutes = direction * (Number(match[2]) * 60 + Number(match[3]))
  const shifted = new Date(date.getTime() + offsetMinutes * 60_000)
  const pad = (number: number) => String(number).padStart(2, '0')
  return `${shifted.getUTCFullYear()}/${pad(shifted.getUTCMonth() + 1)}/${pad(shifted.getUTCDate())} ${pad(shifted.getUTCHours())}:${pad(shifted.getUTCMinutes())}`
}

function schedulerTimezoneLabel(job: ScheduledJob): string {
  const timezone = job.scheduler_timezone || '服务器本地时区'
  const offset = job.scheduler_utc_offset || '+00:00'
  return `${timezone}（UTC${offset}）`
}

function numberValue(value: unknown): number {
  const number = Number(value)
  return Number.isFinite(number) ? Math.max(0, number) : 0
}

async function openLatestExecution(job: ScheduledJob): Promise<void> {
  if (!job.latest_execution_id) return
  await router.push({ name: 'runs', query: { executionId: job.latest_execution_id } })
}

function editJob(job: ScheduledJob): void {
  suspendTargetReset = true
  editingJobId.value = job.id
  form.name = job.name
  form.targetType = job.target_type
  form.scheduleType = job.schedule_type
  form.cronExpression = job.cron_expression || scheduleDefaultExpressions[job.schedule_type]
  form.environmentStrategy = job.environment_strategy
  form.enabled = job.enabled
  form.notifyFeishu = job.notify_feishu
  form.retryCount = job.retry_count
  form.timeoutSeconds = job.timeout_seconds
  selectedTargetIds.value = [...job.target_ids]
  targetSearch.value = ''
  suspendTargetReset = false
}

function resetEditor(): void {
  suspendTargetReset = true
  editingJobId.value = ''
  form.name = ''
  form.targetType = 'baseline_group'
  form.scheduleType = 'daily'
  form.cronExpression = ''
  form.environmentStrategy = 'fixed_revision'
  form.enabled = true
  form.notifyFeishu = false
  form.retryCount = 0
  form.timeoutSeconds = 1800
  selectedTargetIds.value = []
  targetSearch.value = ''
  suspendTargetReset = false
}

async function toggleJobFlag(job: ScheduledJob, flag: 'enabled' | 'notify_feishu'): Promise<void> {
  await scheduledJobs.update(job.id, jobInputFromJob({
    ...job,
    [flag]: !job[flag],
  }))
}

async function deleteJob(job: ScheduledJob): Promise<void> {
  const confirmed = window.confirm(`删除定时任务“${job.name}”？该操作不可恢复。`)
  if (!confirmed) return
  await scheduledJobs.remove(job.id)
  if (editingJobId.value === job.id) resetEditor()
}

function jobInputFromJob(job: ScheduledJob): ScheduledJobInput {
  return {
    project_id: job.project_id,
    source_revision_id: job.source_revision_id,
    environment_revision_id: job.environment_revision_id,
    environment_id: job.environment_id,
    name: job.name,
    target_type: job.target_type,
    target_ids: [...job.target_ids],
    schedule_type: job.schedule_type,
    cron_expression: job.cron_expression || scheduleDefaultExpressions[job.schedule_type],
    environment_strategy: job.environment_strategy,
    enabled: job.enabled,
    notify_feishu: job.notify_feishu,
    retry_count: job.retry_count,
    timeout_seconds: job.timeout_seconds,
  }
}

function baselineGroupOptions(): TargetOption[] {
  const groups = new Map<string, ApiBaselineCase[]>()
  for (const item of availableBaselines.value) {
    const name = baselineGroup(item)
    groups.set(name, [...(groups.get(name) || []), item])
  }
  return [...groups.entries()]
    .sort(([left], [right]) => left.localeCompare(right, 'zh-CN'))
    .map(([name, items]) => {
      const sample = items[0]
      const sampleText = sample ? `${sample.case_name || sample.endpoint_summary || sample.path} · ${sample.method} ${sample.path}` : ''
      return {
        id: name,
        title: name,
        subtitle: [`${items.length} 条基线`, sampleText].filter(Boolean).join(' · '),
        meta: '基线分组',
      }
    })
}

function baselineOption(item: ApiBaselineCase): TargetOption {
  const adoptedAt = item.adopted_at ? new Date(item.adopted_at).toLocaleDateString('zh-CN') : '时间未知'
  const origin = item.origin === 'ai' ? 'AI 生成' : item.origin === 'manual' ? '手工' : item.origin || '未知来源'
  return {
    id: item.id,
    title: `${item.case_name || item.endpoint_summary || item.path} · v${item.case_version}`,
    subtitle: `${item.method} ${item.path} · ${origin} · 采纳于 ${adoptedAt}`,
    meta: baselineGroup(item),
  }
}

function caseOption(item: CaseVersion): TargetOption {
  const request = item.request || { method: '', path: '' }
  return {
    id: item.id,
    title: item.name || item.purpose || item.id,
    subtitle: `${request.method || 'GET'} ${request.path || '未记录路径'}`,
    meta: `${item.priority} · v${item.version}`,
  }
}

function taskOption(item: ApiTestTask): TargetOption {
  return {
    id: item.id,
    title: item.name,
    subtitle: `${item.selected_endpoint_ids.length} 个接口 · ${item.state}`,
    meta: '已保存任务',
  }
}

function byTitle(left: TargetOption, right: TargetOption): number {
  return left.title.localeCompare(right.title, 'zh-CN')
}

function describeCronExpression(expression: string): CronValidation {
  const value = expression.trim()
  if (!value) {
    return { valid: false, message: '请输入 5 位 Cron 表达式，例如 0 2 * * *' }
  }
  const parts = value.split(/\s+/)
  if (parts.length !== 5) {
    return { valid: false, message: 'Cron 表达式需要 5 个字段：分钟 小时 日期 月份 星期' }
  }
  const fields = [
    { value: parts[0], label: '分钟', min: 0, max: 59 },
    { value: parts[1], label: '小时', min: 0, max: 23 },
    { value: parts[2], label: '日期', min: 1, max: 31 },
    { value: parts[3], label: '月份', min: 1, max: 12 },
    { value: parts[4], label: '星期', min: 0, max: 7 },
  ]
  for (const field of fields) {
    const error = validateCronField(field.value, field.label, field.min, field.max)
    if (error) return { valid: false, message: error }
  }
  return { valid: true, message: describeCronParts(parts) }
}

function validateCronField(field: string, label: string, min: number, max: number): string {
  if (!field) return `${label}字段不能为空`
  for (const part of field.split(',')) {
    const error = validateCronPart(part, label, min, max)
    if (error) return error
  }
  return ''
}

function validateCronPart(part: string, label: string, min: number, max: number): string {
  const segments = part.split('/')
  if (segments.length > 2 || !segments[0]) return `${label}字段格式不正确`
  if (segments[1] !== undefined) {
    if (!isNumber(segments[1])) return `${label}字段步长格式不正确`
    const step = Number(segments[1])
    if (step < 1 || step > max) return `${label}字段步长超出范围（1-${max}）`
  }
  return validateCronBase(segments[0], label, min, max)
}

function validateCronBase(base: string, label: string, min: number, max: number): string {
  if (base === '*') return ''
  const range = base.split('-')
  if (range.length === 2) {
    if (!isNumber(range[0]) || !isNumber(range[1])) return `${label}字段范围格式不正确`
    const start = Number(range[0])
    const end = Number(range[1])
    if (start > end) return `${label}字段范围起点不能大于终点`
    if (start < min || end > max) return `${label}字段超出范围（${min}-${max}）`
    return ''
  }
  if (range.length > 2 || !isNumber(base)) return `${label}字段格式不正确`
  const number = Number(base)
  if (number < min || number > max) return `${label}字段超出范围（${min}-${max}）`
  return ''
}

function isNumber(value: string): boolean {
  return /^\d+$/.test(value)
}

function describeCronParts(parts: string[]): string {
  const [minute, hour, dayOfMonth, month, dayOfWeek] = parts
  const singleMinute = singleNumber(minute)
  const singleHour = singleNumber(hour)
  if (singleMinute !== null && singleHour !== null) {
    const time = `${String(singleHour).padStart(2, '0')}:${String(singleMinute).padStart(2, '0')}`
    if (dayOfMonth === '*' && month === '*' && dayOfWeek === '*') return `每天 ${time} 执行`
    if (dayOfMonth === '*' && month === '*' && isWeekdayCron(dayOfWeek)) return `工作日 ${time} 执行`
    const weekDay = singleNumber(dayOfWeek)
    if (dayOfMonth === '*' && month === '*' && weekDay !== null) return `每周${weekDayName(weekDay)} ${time} 执行`
    const monthDay = singleNumber(dayOfMonth)
    if (month === '*' && dayOfWeek === '*' && monthDay !== null) return `每月 ${monthDay} 日 ${time} 执行`
    const monthNumber = singleNumber(month)
    if (dayOfWeek === '*' && monthDay !== null && monthNumber !== null) return `每年 ${monthNumber} 月 ${monthDay} 日 ${time} 执行`
  }
  const minuteStep = everyStep(minute)
  if (minuteStep && hour === '*' && dayOfMonth === '*' && month === '*' && dayOfWeek === '*') {
    return `每 ${minuteStep} 分钟执行`
  }
  return `合法 Cron：分钟 ${minute}，小时 ${hour}，日期 ${dayOfMonth}，月份 ${month}，星期 ${dayOfWeek}`
}

function singleNumber(value: string): number | null {
  return isNumber(value) ? Number(value) : null
}

function isWeekdayCron(value: string): boolean {
  return value === '1-5' || value === '1,2,3,4,5'
}

function everyStep(value: string): number | null {
  const match = value.match(/^\*\/(\d+)$/)
  return match ? Number(match[1]) : null
}

function weekDayName(value: number): string {
  return ['日', '一', '二', '三', '四', '五', '六', '日'][value] || String(value)
}
</script>

<template>
  <section class="workspace scheduled-workspace">
    <header class="page-toolbar">
      <div>
        <p class="eyebrow">定时任务</p>
        <h1>定时任务</h1>
        <p class="page-subtitle">定时任务独立保存项目、目标和环境策略；手动执行会生成带“定时任务”来源的执行记录。</p>
      </div>
      <button type="button" class="secondary-command" data-testid="scheduled-refresh" :disabled="!projectId || scheduledJobs.loading" @click="refreshAll">
        <RefreshCw :size="15" :class="{ 'is-spinning': scheduledJobs.loading }" />刷新
      </button>
    </header>

    <p v-if="scheduledJobs.error" class="inline-error">{{ scheduledJobs.error }}</p>

    <div class="scheduled-layout">
      <section class="scheduled-list">
        <header class="panel-header"><h2>任务列表</h2><span>{{ scheduledJobs.items.length }}</span></header>
        <article v-for="job in scheduledJobs.items" :key="job.id" :data-testid="`scheduled-row-${job.id}`" class="scheduled-row">
          <div class="scheduled-row-main">
            <strong>{{ job.name }}</strong>
            <span>{{ targetTypeLabel(job.target_type) }} · {{ scheduleLabel(job) }} · 调度时区 {{ schedulerTimezoneLabel(job) }} · {{ job.notify_feishu ? '飞书通知' : '不通知' }}</span>
            <span class="scheduled-runtime-line">
              <b>{{ job.enabled ? `下次执行 ${formatDateTime(job.next_run_at, job.scheduler_utc_offset)}` : '当前已停用' }}</b>
              <b v-if="job.latest_run_at">最近{{ triggerLabel(job.latest_run_trigger) }} {{ formatDateTime(job.latest_run_at, job.scheduler_utc_offset) }} · {{ scheduleExecutionSummary(job) }}</b>
              <b v-else>尚无执行记录</b>
            </span>
            <small>{{ jobTargetSummary(job) }}</small>
          </div>
          <div class="scheduled-row-actions">
            <button :data-testid="`scheduled-list-enabled-${job.id}`" type="button" class="mini-switch" :class="{ active: job.enabled }" title="启用" @click="toggleJobFlag(job, 'enabled')">
              <span class="mini-switch-text">启用</span><span class="mini-switch-track"><span class="mini-switch-dot" /></span>
            </button>
            <button :data-testid="`scheduled-list-notify-${job.id}`" type="button" class="mini-switch" :class="{ active: job.notify_feishu }" title="飞书通知" @click="toggleJobFlag(job, 'notify_feishu')">
              <span class="mini-switch-text">飞书</span><span class="mini-switch-track"><span class="mini-switch-dot" /></span>
            </button>
            <button :data-testid="`scheduled-edit-${job.id}`" type="button" class="mini-icon" title="编辑" @click="editJob(job)"><Pencil :size="14" /></button>
            <button :data-testid="`scheduled-delete-${job.id}`" type="button" class="mini-icon danger" title="删除" @click="deleteJob(job)"><Trash2 :size="14" /></button>
            <button v-if="job.latest_execution_id" :data-testid="`scheduled-latest-execution-${job.id}`" type="button" class="mini-icon" title="查看最近执行" @click="openLatestExecution(job)"><ExternalLink :size="14" /></button>
            <button :data-testid="`scheduled-run-${job.id}`" type="button" class="secondary-command" :disabled="scheduledJobs.runningId === job.id" @click="runJob(job)">
              <Play :size="14" />{{ scheduledJobs.runningId === job.id ? '投递中' : '手动执行一次' }}
            </button>
          </div>
        </article>
        <p v-if="!scheduledJobs.items.length" class="section-empty">暂无定时任务。</p>
      </section>

      <section class="scheduled-editor">
        <header class="panel-header">
          <h2>{{ editorTitle }}</h2>
          <button v-if="editingJobId" type="button" class="text-command" data-testid="scheduled-new" @click="resetEditor">新建</button>
          <CalendarClock v-else :size="17" />
        </header>
        <div class="setup-grid two">
          <label>任务名称<input v-model="form.name" data-testid="scheduled-name" placeholder="例如：每日发版回归" /></label>
          <label>目标类型
            <select v-model="form.targetType" data-testid="scheduled-target-type">
              <option value="baseline_group">基线分组</option>
              <option value="baselines">多条基线</option>
              <option value="cases">多个用例</option>
              <option value="task">已保存任务</option>
            </select>
          </label>
          <section class="target-picker wide">
            <header>
              <div>
                <strong>{{ targetPickerTitle }}</strong>
                <span>{{ targetPickerSummary }}</span>
              </div>
              <input v-model="targetSearch" data-testid="scheduled-target-search" :placeholder="targetSearchPlaceholder" />
            </header>
            <p v-if="targetLoadError" class="compact-empty">{{ targetLoadError }}</p>
            <div v-else class="target-option-list">
              <template v-if="form.targetType === 'baselines'">
                <div v-for="group in filteredBaselineGroups" :key="group.name" class="target-group">
                  <button data-testid="scheduled-target-group-toggle" type="button" class="target-group-head" :aria-expanded="targetGroupExpanded(group.name)" @click="toggleTargetGroup(group.name)"><ChevronDown v-if="targetGroupExpanded(group.name)" :size="14" /><ChevronRight v-else :size="14" /><strong>{{ group.name }}</strong><span>{{ group.options.length }}</span></button>
                  <template v-if="targetGroupExpanded(group.name)">
                    <button
                      v-for="option in group.options"
                      :key="`${form.targetType}-${option.id}`"
                      type="button"
                      class="target-option"
                      :class="{ active: isTargetSelected(option.id) }"
                      data-testid="scheduled-target-option"
                      @click="toggleTarget(option.id)"
                    >
                      <span class="target-check"><Check v-if="isTargetSelected(option.id)" :size="13" /></span>
                      <span class="target-copy"><strong>{{ option.title }}</strong><small>{{ option.subtitle }}</small></span>
                      <b>{{ option.meta }}</b>
                    </button>
                  </template>
                </div>
              </template>
              <template v-else>
                <button
                  v-for="option in filteredTargetOptions"
                  :key="`${form.targetType}-${option.id}`"
                  type="button"
                  class="target-option"
                  :class="{ active: isTargetSelected(option.id) }"
                  data-testid="scheduled-target-option"
                  @click="toggleTarget(option.id)"
                >
                  <span class="target-check"><Check v-if="isTargetSelected(option.id)" :size="13" /></span>
                  <span class="target-copy"><strong>{{ option.title }}</strong><small>{{ option.subtitle }}</small></span>
                  <b>{{ option.meta }}</b>
                </button>
              </template>
              <p v-if="!filteredTargetOptions.length" class="section-empty">暂无可选目标。</p>
            </div>
          </section>
          <fieldset class="schedule-picker wide">
            <legend>周期</legend>
            <div class="schedule-segments">
              <button
                v-for="option in scheduleOptions"
                :key="option.value"
                :data-testid="`scheduled-schedule-${option.value}`"
                type="button"
                :class="{ active: form.scheduleType === option.value }"
                @click="setScheduleType(option.value)"
              >{{ option.label }}</button>
            </div>
            <p class="schedule-time-note">{{ scheduleDescription }}</p>
          </fieldset>
          <section v-if="form.scheduleType === 'cron'" class="cron-preset-panel wide">
            <label>Cron 表达式<input v-model="form.cronExpression" data-testid="scheduled-cron" placeholder="0 2 * * *" /></label>
            <p class="cron-feedback" :class="{ invalid: !cronValidation.valid }">{{ cronValidation.message }}</p>
            <div class="cron-preset-grid">
              <button
                v-for="preset in cronPresets"
                :key="preset.id"
                :data-testid="`scheduled-cron-${preset.id}`"
                type="button"
                :class="{ active: form.cronExpression === preset.expression }"
                @click="applyCronPreset(preset.expression)"
              >
                <strong>{{ preset.title }}</strong>
                <code>{{ preset.expression }}</code>
                <span>{{ preset.description }}</span>
              </button>
            </div>
          </section>
          <label>环境策略
            <select v-model="form.environmentStrategy">
              <option value="fixed_revision">固定当前环境版本</option>
              <option value="latest_environment">执行时取环境最新版本</option>
            </select>
          </label>
          <label>失败重试<input v-model.number="form.retryCount" type="number" min="0" max="5" /></label>
          <label>超时秒数<input v-model.number="form.timeoutSeconds" type="number" min="30" max="86400" /></label>
          <button type="button" class="setting-toggle" :class="{ active: form.enabled }" role="switch" :aria-checked="form.enabled" data-testid="scheduled-enabled-toggle" @click="form.enabled = !form.enabled">
            <span class="switch-track"><span class="switch-thumb"><Check :size="10" /></span></span>
            <span><strong>启用</strong><small>保存为启用状态</small></span>
          </button>
          <button type="button" class="setting-toggle" :class="{ active: form.notifyFeishu }" role="switch" :aria-checked="form.notifyFeishu" data-testid="scheduled-notify-toggle" @click="form.notifyFeishu = !form.notifyFeishu">
            <span class="switch-track"><span class="switch-thumb"><Check :size="10" /></span></span>
            <span><strong>飞书通知</strong><small>使用当前项目机器人</small></span>
          </button>
        </div>
        <footer class="notification-actions">
          <span>当前项目：{{ context.projects.find(item => item.id === projectId)?.name || '未选择' }}</span>
          <button data-testid="scheduled-save" type="button" class="primary-command" :disabled="scheduledJobs.saving" @click="saveJob"><Save :size="14" />{{ saveLabel }}</button>
        </footer>
      </section>
    </div>
  </section>
</template>
