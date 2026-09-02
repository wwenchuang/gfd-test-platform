<script setup lang="ts">
import { computed, nextTick, onMounted, reactive, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { CalendarClock, Check, ChevronDown, ChevronRight, ExternalLink, Pencil, Play, RefreshCw, Save, Trash2 } from 'lucide-vue-next'

import type { ApiBaselineCase, ApiTestTask, CaseVersion, ScheduledJob } from '../api/contracts'
import { baselineGroup, useBaselinesStore } from '../stores/baselines'
import { useCasesStore } from '../stores/cases'
import { useContextStore } from '../stores/context'
import { type ScheduledJobInput, useScheduledJobsStore } from '../stores/scheduledJobs'
import { useTasksStore } from '../stores/tasks'
import { hasExplicitOneTimeMarker } from '../utils/caseClassification'
import { confirmApiExecution } from '../utils/executionConfirmation'
import { formatPassRate, statusLabel } from '../utils/executionPresentation'
import { taskStateLabel } from '../utils/taskPresentation'
import { applicationBusinessLabel, applicationBusinessSelection } from '../utils/testApplications'

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
  allowOneTimeBaselines: false,
  retryCount: 0,
  timeoutSeconds: 1800,
})
const selectedTargetIds = ref<string[]>([])
const targetSearch = ref('')
const targetLoadError = ref('')
const targetsLoading = ref(true)
const loadedCaseSourceId = ref('')
const refreshing = ref(false)
const actionMessage = ref('')
const nameInput = ref<HTMLInputElement | null>(null)
const editorScope = ref<{ projectId: string; sourceRevisionId: string; environmentRevisionId: string; environmentId: string } | null>(null)
const busy = computed(() => scheduledJobs.saving || Boolean(scheduledJobs.removingId) || Boolean(scheduledJobs.runningId))
const editingJobId = ref('')
const expandedTargetGroups = ref<Set<string>>(new Set())
let suspendTargetReset = false

interface TargetOption {
  id: string
  title: string
  subtitle: string
  meta: string
  scope?: string
  baselineCount?: number
  selectable: boolean
  unavailableReason: string
}

interface CronValidation {
  valid: boolean
  message: string
}

const scheduleOptions: Array<{ value: ScheduledJob['schedule_type']; label: string }> = [
  { value: 'daily', label: '每天 02:00' },
  { value: 'weekly', label: '每周一 09:00' },
  { value: 'cron', label: '自定义表达式' },
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

const projectId = computed(() => editorScope.value ? editorScope.value.projectId : context.projectId || context.projects[0]?.id || '')
const sourceRevisionId = computed(() => editorScope.value ? editorScope.value.sourceRevisionId : context.sourceRevisionId || context.sourceRevisions.find(item => item.project_id === projectId.value)?.id || '')
const environmentRevisionId = computed(() => editorScope.value ? editorScope.value.environmentRevisionId : context.environmentRevisionId || context.environmentRevisions.find(item => item.project_id === projectId.value)?.id || '')
const environmentId = computed(() => editorScope.value ? editorScope.value.environmentId : context.environmentRevisions.find(item => item.id === environmentRevisionId.value)?.environment_id || '')
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
const availableBaselines = computed(() => baselines.items.filter(item => item.status === 'active'))
const supportsOneTimeBaselines = computed(() => ['baselines', 'baseline_group', 'task'].includes(form.targetType))
const selectedOneTimeBaselineCount = computed(() => oneTimeBaselineCount(form.targetType, selectedTargetIds.value))
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
    const name = option.scope || '未标注应用 · 未标注业务'
    groups.set(name, [...(groups.get(name) || []), option])
  }
  return [...groups.entries()]
    .sort(([left], [right]) => left.localeCompare(right, 'zh-CN'))
    .map(([name, options]) => ({
      name,
      options,
      baselineCount: options.reduce((total, option) => total + (option.baselineCount || 0), 0),
    }))
})
const pendingFixedCaseCount = computed(() => editingJobId.value && form.targetType === 'cases' ? selectedTargetIds.value.filter(id => !cases.versions[id]).length : 0)
const pendingMissingTargetIds = computed(() => {
  if (!editingJobId.value || targetsLoading.value || targetLoadError.value || form.targetType === 'cases') return []
  const knownIds = new Set(targetOptions.value.map(option => option.id))
  return selectedTargetIds.value.filter(id => !knownIds.has(id))
})
const missingBaselineReplacements = computed(() => {
  const replacements = new Map<string, string>()
  if (form.targetType !== 'baselines') return replacements
  for (const missingId of pendingMissingTargetIds.value) {
    const retired = baselines.items.find(item => item.id === missingId)
    if (!retired) continue
    const current = availableBaselines.value
      .filter(item => (
        item.case_id === retired.case_id
        && item.source_revision_id === retired.source_revision_id
        && baselineOption(item).selectable
      ))
      .sort((left, right) => right.case_version - left.case_version)[0]
    if (current) replacements.set(missingId, current.id)
  }
  return replacements
})
const canReplaceAllMissingBaselines = computed(() => (
  Boolean(pendingMissingTargetIds.value.length)
  && missingBaselineReplacements.value.size === pendingMissingTargetIds.value.length
))
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

onMounted(() => refreshAll(false))

watch(() => form.targetType, () => {
  if (suspendTargetReset) return
  selectedTargetIds.value = []
  form.allowOneTimeBaselines = false
  targetSearch.value = ''
  expandedTargetGroups.value = new Set()
  if (form.targetType === 'baseline_group') {
    void nextTick(() => {
      expandedTargetGroups.value = new Set(filteredBaselineGroups.value.map(group => group.name))
    })
  }
}, { flush: 'sync' })

watch(() => [
  form.name,
  form.targetType,
  form.scheduleType,
  form.cronExpression,
  form.environmentStrategy,
  form.retryCount,
  form.timeoutSeconds,
  form.allowOneTimeBaselines,
  selectedTargetIds.value.join('|'),
], () => {
  if (busy.value) return
  scheduledJobs.error = ''
  actionMessage.value = ''
}, { flush: 'sync' })

function targetIds(): string[] {
  return [...selectedTargetIds.value]
}

async function saveJob(): Promise<void> {
  if (busy.value) return
  scheduledJobs.error = ''
  actionMessage.value = ''
  if (!form.name.trim()) {
    scheduledJobs.error = '请输入任务名称'
    focusEditor()
    return
  }
  if (!projectId.value || !sourceRevisionId.value || (form.environmentStrategy === 'fixed_revision' ? !environmentRevisionId.value : !environmentId.value)) {
    scheduledJobs.error = '请先选择项目、接口版本和执行环境；编辑历史任务时保留原范围，不会自动改用工作台当前环境'
    return
  }
  if (targetsLoading.value || targetLoadError.value) {
    scheduledJobs.error = targetsLoading.value ? '正在读取目标，请稍后保存' : `目标读取失败：${targetLoadError.value}。请刷新后重试`
    return
  }
  if (pendingMissingTargetIds.value.length) {
    scheduledJobs.error = `当前选择包含 ${pendingMissingTargetIds.value.length} 个已失效目标，请先移除失效目标并选择替代项`
    return
  }
  const ids = targetIds()
  if (!ids.length) {
    scheduledJobs.error = `请先${targetPickerTitle.value}`
    return
  }
  if (selectedOneTimeBaselineCount.value && !form.allowOneTimeBaselines) {
    scheduledJobs.error = `所选目标包含 ${selectedOneTimeBaselineCount.value} 条一次性基线。请明确开启“一次性基线也执行”，或重新选择目标。`
    return
  }
  if (!Number.isInteger(form.retryCount) || form.retryCount < 0 || form.retryCount > 5) {
    scheduledJobs.error = '失败重试次数必须是 0 到 5 的整数'
    return
  }
  if (!Number.isInteger(form.timeoutSeconds) || form.timeoutSeconds < 30 || form.timeoutSeconds > 86400) {
    scheduledJobs.error = '超时秒数必须是 30 到 86400 的整数'
    return
  }
  if (form.scheduleType === 'cron' && !cronValidation.value.valid) {
    scheduledJobs.error = cronValidation.value.message
    return
  }
  const input = buildJobInput(ids)
  try {
    if (editingJobId.value) await scheduledJobs.update(editingJobId.value, input)
    else await scheduledJobs.create(input)
    resetEditor()
    actionMessage.value = `定时任务“${input.name}”已保存（${input.enabled ? '已启用' : '已停用'}，${input.notify_feishu ? '开启通知' : '不通知'}）。可在任务列表查看或手动执行一次。`
  } catch { /* Store retains the actionable error; keep the user's draft. */ }
}

function focusEditor(): void {
  void nextTick(() => {
    nameInput.value?.scrollIntoView?.({ block: 'center', behavior: 'smooth' })
    nameInput.value?.focus({ preventScroll: true })
  })
}

const scopeLabel = computed(() => {
  const source = context.sourceRevisions.find(item => item.id === sourceRevisionId.value)
  const environment = context.environmentRevisions.find(item => item.id === environmentRevisionId.value)
    || context.environmentRevisions.find(item => item.environment_id === environmentId.value)
  return `${context.projects.find(item => item.id === projectId.value)?.name || '未选择项目'} · ${source ? `接口 v${source.revision_number}` : '已保存的历史接口版本'} · ${environment?.name || '已保存的历史环境'}${form.environmentStrategy === 'latest_environment' ? '（执行时取最新版本）' : environment ? ` v${environment.revision}` : '（固定原版本）'}`
})

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
    allow_one_time_baselines: form.allowOneTimeBaselines,
    retry_count: form.retryCount,
    timeout_seconds: form.timeoutSeconds,
  }
}

async function runJob(job: ScheduledJob): Promise<void> {
  if (busy.value || targetsLoading.value) return
  actionMessage.value = ''
  const targetIssue = jobTargetIssue(job)
  if (targetIssue) {
    scheduledJobs.error = `定时任务“${job.name}”执行已阻断：${targetIssue}。请编辑任务并重新选择有效目标。`
    return
  }
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
  try {
    const execution = await scheduledJobs.runOnce(job.id)
    await router.push({ name: 'runs', query: { executionId: execution.id } })
  } catch { /* Store exposes the request failure. */ }
}

async function loadTargetAssets(): Promise<void> {
  targetsLoading.value = true
  targetLoadError.value = ''
  try {
    if (!projectId.value) return
    const loadingSource = sourceRevisionId.value
    const results = await Promise.allSettled([
      baselines.load({ projectId: projectId.value, sourceRevisionId: sourceRevisionId.value, environmentRevisionId: environmentRevisionId.value }),
      tasks.list(projectId.value),
      sourceRevisionId.value ? cases.loadSavedCases(sourceRevisionId.value) : Promise.resolve(),
    ])
    if (results[2]?.status === 'fulfilled') loadedCaseSourceId.value = loadingSource
    const rejected = results.find(item => item.status === 'rejected')
    targetLoadError.value = baselines.error || tasks.error || (rejected?.status === 'rejected'
      ? rejected.reason instanceof Error ? rejected.reason.message : '无法读取可选目标'
      : '')
  } finally {
    targetsLoading.value = false
    if (form.targetType === 'baseline_group') {
      expandedTargetGroups.value = new Set(filteredBaselineGroups.value.map(group => group.name))
    }
  }
}

async function refreshAll(announce = true): Promise<void> {
  if (refreshing.value || busy.value) return
  refreshing.value = true
  actionMessage.value = ''
  try {
    await Promise.all([context.loadSavedContext(), context.loadOptions()])
    if (context.error) throw new Error(context.error)
    await Promise.all([
      projectId.value ? scheduledJobs.load(projectId.value) : Promise.resolve(),
      loadTargetAssets(),
    ])
    if (announce && !scheduledJobs.error && !targetLoadError.value) actionMessage.value = `已刷新 ${scheduledJobs.items.length} 个定时任务及可选目标`
  } catch (error) {
    scheduledJobs.error = error instanceof Error ? error.message : '定时任务读取失败，请重试'
    targetLoadError.value = scheduledJobs.error
  } finally { refreshing.value = false; targetsLoading.value = false }
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
  const option = targetOptions.value.find(item => item.id === id)
  if (option && !option.selectable && !isTargetSelected(id)) return
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

function canToggleTarget(option: TargetOption): boolean {
  return option.selectable || isTargetSelected(option.id)
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

function selectableTargetGroupIds(options: TargetOption[]): string[] {
  return options.filter(option => option.selectable).map(option => option.id)
}

function selectedTargetGroupCount(options: TargetOption[]): number {
  const ids = new Set(selectableTargetGroupIds(options))
  return selectedTargetIds.value.filter(id => ids.has(id)).length
}

function allTargetGroupSelected(options: TargetOption[]): boolean {
  const ids = selectableTargetGroupIds(options)
  return Boolean(ids.length) && ids.every(id => isTargetSelected(id))
}

function toggleTargetGroupSelection(options: TargetOption[]): void {
  const ids = selectableTargetGroupIds(options)
  if (!ids.length) return
  const groupIds = new Set(ids)
  if (ids.every(id => isTargetSelected(id))) {
    selectedTargetIds.value = selectedTargetIds.value.filter(id => !groupIds.has(id))
    return
  }
  selectedTargetIds.value = [...new Set([...selectedTargetIds.value, ...ids])]
}

function removeMissingTargets(): void {
  const missing = new Set(pendingMissingTargetIds.value)
  if (!missing.size) return
  selectedTargetIds.value = selectedTargetIds.value.filter(id => !missing.has(id))
  actionMessage.value = `已移除 ${missing.size} 个失效目标，请选择替代目标后保存。`
}

function replaceMissingBaselineTargets(): void {
  if (!canReplaceAllMissingBaselines.value) return
  const replacements = new Map(missingBaselineReplacements.value)
  selectedTargetIds.value = [...new Set(selectedTargetIds.value.map(id => replacements.get(id) || id))]
  actionMessage.value = `已将 ${replacements.size} 个失效基线替换为当前有效版本，请保存任务后恢复执行。`
}

function jobTargetSummary(job: ScheduledJob): string {
  if (targetsLoading.value) return `已选 ${job.target_ids.length} 项，正在读取目标`
  if (targetLoadError.value) return `目标读取失败：${targetLoadError.value}。请刷新后重试`
  if (job.target_type === 'cases' && (job.source_revision_id !== loadedCaseSourceId.value || job.target_ids.some(id => !cases.versions[id]))) return `已选 ${job.target_ids.length} 项固定用例版本，使用任务原接口版本；当前列表未加载全部历史版本，保存与执行仍保留固定版本，由服务端核验`
  const optionMap = new Map(targetOptionsForType(job.target_type).map(option => [option.id, option]))
  const resolved = job.target_ids.map(id => optionMap.get(id)).filter((item): item is TargetOption => Boolean(item))
  if (!resolved.length) return `已选 ${job.target_ids.length} 项，未找到可用目标，请编辑重新选择`
  const labels = resolved.slice(0, 2).map(option => `${option.title}${option.meta ? ` · ${option.meta}` : ''}`)
  const remaining = job.target_ids.length - labels.length
  return `${labels.join('；')}${remaining > 0 ? `；另 ${remaining} 项` : ''}`
}

function jobTargetIssue(job: ScheduledJob): string {
  if (targetsLoading.value) return '正在读取目标，请稍候'
  if (targetLoadError.value) return '目标读取失败，请刷新后重试'
  const optionMap = new Map(targetOptionsForType(job.target_type).map(option => [option.id, option]))
  const missingIds = job.target_ids.filter(id => !optionMap.has(id))
  if (missingIds.length && job.target_type === 'cases') return ''
  if (missingIds.length) return `${missingIds.length} 个目标已删除或不属于当前项目`
  const blocked = job.target_ids.map(id => optionMap.get(id)).find(option => option && !option.selectable)
  if (blocked?.unavailableReason) return blocked.unavailableReason
  const oneTimeCount = oneTimeBaselineCount(job.target_type, job.target_ids)
  if (oneTimeCount && !job.allow_one_time_baselines) {
    return `目标包含 ${oneTimeCount} 条一次性基线，请编辑任务并明确开启“一次性基线也执行”`
  }
  return ''
}

function scheduledBlockMessage(job: ScheduledJob): string {
  switch (job.blocked_reason) {
    case undefined:
    case '':
      return ''
    case 'blocked: permission or scope revoked':
      return '保存任务配置的成员的执行权限或数据范围已撤销。请联系管理员恢复保存任务配置的成员对项目、环境及执行操作的授权，再手动执行一次并刷新结果。'
    case 'blocked: scheduled target unavailable or outside current scope':
      return '定时任务目标不可用，或已超出当前数据范围。请检查目标及环境是否有效，并编辑任务重新选择；若授权已撤销，请联系管理员恢复。完成后可手动执行一次并刷新结果。'
    default:
      return '服务端阻断原因尚未识别。请联系管理员检查任务权限、目标和环境后重试。'
  }
}

const editingBlockMessage = computed(() => {
  const job = scheduledJobs.items.find(item => item.id === editingJobId.value)
  return job ? scheduledBlockMessage(job) : ''
})

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
  if (passed < total) return `未通过 · ${passed}/${total} 通过 · ${formatPassRate(passed, total)}`
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
  if (busy.value || targetsLoading.value) return
  scheduledJobs.error = ''
  actionMessage.value = ''
  const oldSource = sourceRevisionId.value
  editorScope.value = {
    projectId: job.project_id,
    sourceRevisionId: job.source_revision_id || '',
    environmentRevisionId: job.environment_revision_id || context.environmentRevisions.find(item => item.environment_id === job.environment_id)?.id || '',
    environmentId: job.environment_id || context.environmentRevisions.find(item => item.id === job.environment_revision_id)?.environment_id || '',
  }
  suspendTargetReset = true
  editingJobId.value = job.id
  form.name = job.name
  form.targetType = job.target_type
  form.scheduleType = job.cron_expression && job.cron_expression !== scheduleDefaultExpressions[job.schedule_type] ? 'cron' : job.schedule_type
  form.cronExpression = job.cron_expression || scheduleDefaultExpressions[job.schedule_type]
  form.environmentStrategy = job.environment_strategy
  form.enabled = job.enabled
  form.notifyFeishu = job.notify_feishu
  form.allowOneTimeBaselines = Boolean(job.allow_one_time_baselines)
  form.retryCount = job.retry_count
  form.timeoutSeconds = job.timeout_seconds
  selectedTargetIds.value = [...job.target_ids]
  targetSearch.value = ''
  suspendTargetReset = false
  focusEditor()
  if (oldSource !== sourceRevisionId.value) void loadTargetAssets()
}

function resetEditor(): void {
  if (busy.value) return
  const oldSource = sourceRevisionId.value
  const oldProject = projectId.value
  editorScope.value = null
  scheduledJobs.error = ''
  actionMessage.value = ''
  suspendTargetReset = true
  editingJobId.value = ''
  form.name = ''
  form.targetType = 'baseline_group'
  form.scheduleType = 'daily'
  form.cronExpression = ''
  form.environmentStrategy = 'fixed_revision'
  form.enabled = true
  form.notifyFeishu = false
  form.allowOneTimeBaselines = false
  form.retryCount = 0
  form.timeoutSeconds = 1800
  selectedTargetIds.value = []
  targetSearch.value = ''
  suspendTargetReset = false
  if (oldProject !== projectId.value) void refreshAll(false)
  else if (oldSource !== sourceRevisionId.value) void loadTargetAssets()
}

async function toggleJobFlag(job: ScheduledJob, flag: 'enabled' | 'notify_feishu'): Promise<void> {
  if (busy.value) return
  actionMessage.value = ''
  if (flag === 'enabled' && !job.enabled && jobTargetIssue(job)) {
    scheduledJobs.error = `无法启用定时任务“${job.name}”：${jobTargetIssue(job)}。请先编辑并重新选择有效目标。`
    return
  }
  try {
    await scheduledJobs.update(job.id, jobInputFromJob({ ...job, [flag]: !job[flag] }))
    actionMessage.value = `定时任务“${job.name}”${flag === 'enabled' ? (job.enabled ? '已停用' : '已启用') : (job.notify_feishu ? '已关闭飞书通知' : '已开启飞书通知')}`
    if (editingJobId.value === job.id) {
      if (flag === 'enabled') form.enabled = !job.enabled
      else form.notifyFeishu = !job.notify_feishu
    }
  } catch { /* Store exposes the request failure. */ }
}

async function deleteJob(job: ScheduledJob): Promise<void> {
  if (busy.value) return
  actionMessage.value = ''
  const confirmed = window.confirm(`删除定时任务“${job.name}”？该操作不可恢复。`)
  if (!confirmed) return
  try {
    await scheduledJobs.remove(job.id)
    if (editingJobId.value === job.id) resetEditor()
    actionMessage.value = `定时任务“${job.name}”已删除，已有执行记录仍保留`
  } catch { /* Store exposes the request failure. */ }
}

function jobInputFromJob(job: ScheduledJob): ScheduledJobInput {
  return {
    project_id: job.project_id,
    source_revision_id: job.source_revision_id || undefined,
    environment_revision_id: job.environment_revision_id || undefined,
    environment_id: job.environment_id || undefined,
    name: job.name,
    target_type: job.target_type,
    target_ids: [...job.target_ids],
    schedule_type: job.schedule_type,
    cron_expression: job.cron_expression || scheduleDefaultExpressions[job.schedule_type],
    environment_strategy: job.environment_strategy,
    enabled: job.enabled,
    notify_feishu: job.notify_feishu,
    allow_one_time_baselines: Boolean(job.allow_one_time_baselines),
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
      const unavailableReason = items.map(item => applicationBusinessSelection(item.app_package, item.business))
        .find(selection => !selection.selectable)?.reason || ''
      return {
        id: name,
        title: name,
        subtitle: [`${items.length} 条基线`, sampleText].filter(Boolean).join(' · '),
        meta: `${items.length} 条基线 · 维护分组 · ${name}${unavailableReason ? ` · ${unavailableReason}` : ''}`,
        scope: scopeSummary(items),
        baselineCount: items.length,
        selectable: !unavailableReason,
        unavailableReason,
      }
    })
}

function baselineOption(item: ApiBaselineCase): TargetOption {
  const adoptedAt = item.adopted_at ? new Date(item.adopted_at).toLocaleDateString('zh-CN') : '时间未知'
  const origin = item.origin === 'ai' ? 'AI 生成' : item.origin === 'manual' ? '手工' : item.origin || '未知来源'
  const selection = applicationBusinessSelection(item.app_package, item.business)
  return {
    id: item.id,
    title: `${item.case_name || item.endpoint_summary || item.path} · v${item.case_version}`,
    subtitle: `${item.method} ${item.path} · ${origin} · 采纳于 ${adoptedAt}`,
    meta: `${baselineGroup(item)}${selection.reason ? ` · ${selection.reason}` : ''}`,
    scope: caseScopeLabel(item),
    baselineCount: 1,
    selectable: selection.selectable,
    unavailableReason: selection.reason,
  }
}

function isOneTimeBaseline(item: ApiBaselineCase): boolean {
  return hasExplicitOneTimeMarker([item.case_name, baselineGroup(item), ...item.tags])
}

function oneTimeBaselineCount(type: ScheduledJob['target_type'], targetIds: string[]): number {
  const selected = new Set(targetIds)
  if (type === 'baselines') {
    return availableBaselines.value.filter(item => selected.has(item.id) && isOneTimeBaseline(item)).length
  }
  if (type === 'baseline_group') {
    return availableBaselines.value.filter(item => selected.has(baselineGroup(item)) && isOneTimeBaseline(item)).length
  }
  if (type === 'task') {
    const endpointIds = new Set(
      tasks.tasks
        .filter(item => selected.has(item.id))
        .flatMap(item => item.selected_endpoint_ids),
    )
    return availableBaselines.value.filter(item => endpointIds.has(item.endpoint_id) && isOneTimeBaseline(item)).length
  }
  return 0
}

function caseOption(item: CaseVersion): TargetOption {
  const request = item.request || { method: '', path: '' }
  const selection = applicationBusinessSelection(item.app_package, item.business)
  return {
    id: item.id,
    title: item.name || item.purpose || item.id,
    subtitle: `${request.method || 'GET'} ${request.path || '未记录路径'}`,
    meta: `${caseScopeLabel(item)} · ${item.priority} · v${item.version}${selection.reason ? ` · ${selection.reason}` : ''}`,
    selectable: selection.selectable,
    unavailableReason: selection.reason,
  }
}

function caseScopeLabel(item: Pick<CaseVersion, 'app_package' | 'app_name' | 'business'>): string {
  return applicationBusinessLabel(item.app_package, item.app_name, item.business)
}

function scopeSummary(items: Array<Pick<CaseVersion, 'app_package' | 'app_name' | 'business'>>): string {
  return [...new Set(items.map(caseScopeLabel))].join('；') || '未标注应用 · 未标注业务'
}

function taskOption(item: ApiTestTask): TargetOption {
  const selected = new Set(item.selected_endpoint_ids)
  const runnableBaselines = availableBaselines.value.filter(baseline => selected.has(baseline.endpoint_id))
  const unavailableReason = item.runnable_baseline_count === 0 || !runnableBaselines.length
    ? '当前任务没有可执行基线。请到任务管理编辑范围，调试通过并采纳基线后再选择。'
    : runnableBaselines.map(baseline => applicationBusinessSelection(baseline.app_package, baseline.business))
      .find(selection => !selection.selectable)?.reason || ''
  return {
    id: item.id,
    title: item.name,
    subtitle: `${item.selected_endpoint_ids.length} 个接口 · ${taskStateLabel(item.state, item.runnable_baseline_count)}${item.runnable_baseline_count !== undefined ? ` · ${item.runnable_baseline_count} 条可执行基线` : ''}`,
    meta: `已保存任务 · ${scopeSummary(runnableBaselines)}${unavailableReason ? ` · ${unavailableReason}` : ''}`,
    selectable: !unavailableReason,
    unavailableReason,
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
      <button type="button" class="icon-command scheduled-refresh" data-testid="scheduled-refresh" title="刷新定时任务" aria-label="刷新定时任务" :disabled="refreshing || targetsLoading || busy" @click="refreshAll()">
        <RefreshCw :size="18" :class="{ 'is-spinning': refreshing || targetsLoading }" />
      </button>
    </header>

    <p v-if="scheduledJobs.error" class="inline-error" role="alert">{{ scheduledJobs.error }}</p>

    <p v-if="actionMessage" class="scheduled-feedback" role="status">{{ actionMessage }}</p>
    <p v-if="busy" class="scheduled-feedback" role="status">{{ scheduledJobs.saving ? '正在保存配置…' : scheduledJobs.removingId ? '正在删除任务…' : '正在投递执行任务…' }} 请等待结果，避免重复提交。</p>
    <p v-if="refreshing" class="scheduled-feedback" role="status">正在读取定时任务和可选目标…</p>

    <div class="scheduled-layout">
      <section class="scheduled-list">
        <header class="panel-header"><h2>任务列表</h2><span>{{ scheduledJobs.items.length }}</span></header>
        <article v-for="job in scheduledJobs.items" :key="job.id" :data-testid="`scheduled-row-${job.id}`" class="scheduled-row" :class="{ 'has-server-block': Boolean(scheduledBlockMessage(job)) }">
          <div class="scheduled-row-main">
            <strong>{{ job.name }}</strong>
            <span>{{ targetTypeLabel(job.target_type) }} · {{ scheduleLabel(job) }} · 调度时区 {{ schedulerTimezoneLabel(job) }} · {{ job.notify_feishu ? '飞书通知' : '不通知' }} · {{ job.allow_one_time_baselines ? '一次性基线已允许' : '一次性基线未开启' }}</span>
            <span class="scheduled-runtime-line">
              <b>配置：{{ job.enabled ? '已启用' : '已停用' }}</b>
              <b v-if="!scheduledBlockMessage(job) && targetsLoading">正在校验目标，配置未变</b>
              <b v-else-if="!scheduledBlockMessage(job) && jobTargetIssue(job)" class="scheduled-blocked">执行已阻断 · {{ jobTargetIssue(job) }}</b>
              <b v-else-if="!scheduledBlockMessage(job) && job.enabled">下次执行 {{ formatDateTime(job.next_run_at, job.scheduler_utc_offset) }}</b>
              <b v-if="job.latest_run_at">上次结果（历史）· 最近{{ triggerLabel(job.latest_run_trigger) }} {{ formatDateTime(job.latest_run_at, job.scheduler_utc_offset) }} · {{ scheduleExecutionSummary(job) }}</b>
              <b v-else>尚无执行记录</b>
            </span>
            <p v-if="scheduledBlockMessage(job)" class="inline-error" role="status">执行已阻断 · {{ scheduledBlockMessage(job) }}</p>
            <small :data-testid="`scheduled-current-target-${job.id}`">当前目标：{{ jobTargetSummary(job) }}</small>
          </div>
          <div class="scheduled-row-actions">
            <button :data-testid="`scheduled-list-enabled-${job.id}`" type="button" class="mini-switch" :disabled="busy || targetsLoading" :class="{ active: job.enabled }" role="switch" :aria-checked="job.enabled" :aria-label="job.enabled ? '停用定时任务' : '启用定时任务'" :title="job.enabled ? '停用定时任务' : '启用定时任务'" @click="toggleJobFlag(job, 'enabled')">
              <span class="mini-switch-text">启用</span><span class="mini-switch-track"><span class="mini-switch-dot" /></span>
            </button>
            <button :data-testid="`scheduled-list-notify-${job.id}`" type="button" class="mini-switch" :disabled="busy || targetsLoading" :class="{ active: job.notify_feishu }" role="switch" :aria-checked="job.notify_feishu" :aria-label="job.notify_feishu ? '关闭飞书通知' : '开启飞书通知'" :title="job.notify_feishu ? '关闭飞书通知' : '开启飞书通知'" @click="toggleJobFlag(job, 'notify_feishu')">
              <span class="mini-switch-text">飞书</span><span class="mini-switch-track"><span class="mini-switch-dot" /></span>
            </button>
            <button :data-testid="`scheduled-edit-${job.id}`" type="button" class="mini-icon" :disabled="busy || targetsLoading" title="编辑" @click="editJob(job)"><Pencil :size="14" /></button>
            <button :data-testid="`scheduled-delete-${job.id}`" type="button" class="mini-icon danger" :disabled="busy" title="删除" @click="deleteJob(job)"><Trash2 :size="14" /></button>
            <button v-if="job.latest_execution_id" :data-testid="`scheduled-latest-execution-${job.id}`" type="button" class="mini-icon" title="查看最近执行" @click="openLatestExecution(job)"><ExternalLink :size="14" /></button>
            <button :data-testid="`scheduled-run-${job.id}`" type="button" class="secondary-command" :disabled="busy || Boolean(jobTargetIssue(job))" :title="jobTargetIssue(job) || '立即执行已保存配置，不受启用开关影响'" @click="runJob(job)">
              <Play :size="14" />{{ scheduledJobs.runningId === job.id ? '投递中' : '手动执行一次' }}
            </button>
          </div>
        </article>
        <p v-if="!scheduledJobs.items.length && !refreshing && !scheduledJobs.error" class="section-empty">暂无定时任务。</p>
      </section>

      <section class="scheduled-editor">
        <header class="panel-header">
          <h2>{{ editorTitle }}</h2>
          <button v-if="editingJobId" type="button" class="text-command" data-testid="scheduled-new" :disabled="busy || targetsLoading" @click="resetEditor">取消编辑</button>
          <CalendarClock v-else :size="17" />
        </header>
        <p v-if="editingBlockMessage" data-testid="scheduled-editor-blocked" class="inline-error" role="status">执行已阻断 · {{ editingBlockMessage }}</p>
        <p class="scheduled-scope" data-testid="scheduled-scope">{{ editingJobId ? '保留任务原范围' : '新任务使用当前范围' }}：{{ scopeLabel }}。<router-link v-if="!editingJobId" to="/">去工作台调整范围</router-link></p>
        <fieldset class="setup-grid two scheduled-form" :disabled="busy">

          <label>任务名称<input ref="nameInput" v-model="form.name" data-testid="scheduled-name" placeholder="例如：每日发版回归" /></label>
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
            <p v-if="pendingFixedCaseCount && !targetsLoading" class="compact-empty">已保留 {{ pendingFixedCaseCount }} 个原固定用例版本；它们未出现在最新用例列表中，不代表已删除。保存不会自动升级版本，执行时服务端仍会校验。</p>
            <p v-if="pendingMissingTargetIds.length" class="compact-empty" data-testid="scheduled-missing-targets">
              当前选择包含 {{ pendingMissingTargetIds.length }} 个已失效目标，不能继续执行。
              <button v-if="canReplaceAllMissingBaselines" type="button" class="text-command" data-testid="scheduled-replace-missing-targets" @click="replaceMissingBaselineTargets">替换为当前有效版本</button>
              <button type="button" class="text-command" data-testid="scheduled-remove-missing-targets" @click="removeMissingTargets">移除失效目标</button>
              <template v-if="canReplaceAllMissingBaselines">替换会沿用同一用例的最新已采纳版本。</template>
              <template v-else>移除后请在下方选择替代目标再保存。</template>
            </p>
            <p v-if="targetsLoading" class="compact-empty" role="status">正在读取目标…</p>
            <p v-else-if="targetLoadError" class="compact-empty" role="alert">{{ targetLoadError }}</p>
            <div v-else class="target-option-list">
              <template v-if="form.targetType === 'baselines' || form.targetType === 'baseline_group'">
                <div v-for="group in filteredBaselineGroups" :key="group.name" class="target-group">
                  <div class="target-group-head">
                    <button data-testid="scheduled-target-group-toggle" type="button" class="target-group-toggle" :aria-expanded="targetGroupExpanded(group.name)" @click="toggleTargetGroup(group.name)"><ChevronDown v-if="targetGroupExpanded(group.name)" :size="14" /><ChevronRight v-else :size="14" /><strong>{{ group.name }}</strong><span>{{ group.baselineCount }} 条基线</span></button>
                    <small>已选 {{ selectedTargetGroupCount(group.options) }}/{{ selectableTargetGroupIds(group.options).length }}</small>
                    <button
                      data-testid="scheduled-target-group-select"
                      type="button"
                      class="text-command target-group-select"
                      :disabled="!selectableTargetGroupIds(group.options).length"
                      @click="toggleTargetGroupSelection(group.options)"
                    >{{ group.name }} · {{ allTargetGroupSelected(group.options) ? '清空本业务' : '全选本业务' }}</button>
                  </div>
                  <template v-if="targetGroupExpanded(group.name)">
                    <button
                      v-for="option in group.options"
                      :key="`${form.targetType}-${option.id}`"
                      type="button"
                      class="target-option"
                      :class="{ active: isTargetSelected(option.id), unavailable: !option.selectable }"
                      :disabled="!canToggleTarget(option)"
                      :title="option.unavailableReason"
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
                  :class="{ active: isTargetSelected(option.id), unavailable: !option.selectable }"
                  :disabled="!canToggleTarget(option)"
                  :title="option.unavailableReason"
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
            <p class="schedule-switch-note">切换“每天 02:00”或“每周一 09:00”会使用按钮标注的默认时间；其他执行时间请保留或选择“自定义表达式”。</p>
          </fieldset>
          <section v-if="form.scheduleType === 'cron'" class="cron-preset-panel wide">
            <label>自定义表达式（Cron）<input v-model="form.cronExpression" data-testid="scheduled-cron" placeholder="例如：0 2 * * *" /></label>
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
          <button v-if="supportsOneTimeBaselines" type="button" class="setting-toggle" :class="{ active: form.allowOneTimeBaselines }" role="switch" :aria-checked="form.allowOneTimeBaselines" data-testid="scheduled-one-time-toggle" @click="form.allowOneTimeBaselines = !form.allowOneTimeBaselines">
            <span class="switch-track"><span class="switch-thumb"><Check :size="10" /></span></span>
            <span><strong>一次性基线也执行</strong><small>显式允许该任务重复执行一次性用例</small></span>
          </button>
          <p v-if="supportsOneTimeBaselines && selectedOneTimeBaselineCount" class="compact-empty wide" data-testid="scheduled-one-time-warning">当前目标包含 {{ selectedOneTimeBaselineCount }} 条一次性基线。开启后，手动触发和每天调度都会执行；请确保前置和清理步骤可重复。</p>
        </fieldset>
        <p v-if="scheduledJobs.error || actionMessage" data-testid="scheduled-editor-feedback" :class="scheduledJobs.error ? 'inline-error' : 'scheduled-feedback'" :role="scheduledJobs.error ? 'alert' : 'status'">{{ scheduledJobs.error || actionMessage }}</p>
        <footer class="notification-actions">
          <span>当前项目：{{ context.projects.find(item => item.id === projectId)?.name || '未选择' }}</span>
          <button data-testid="scheduled-save" type="button" class="primary-command" :disabled="busy || targetsLoading" @click="saveJob"><Save :size="14" />{{ saveLabel }}</button>
        </footer>
      </section>
    </div>
  </section>
</template>

<style scoped>
.scheduled-form { border: 0; margin: 0; min-width: 0; }
.scheduled-feedback, .scheduled-scope { margin: 10px 13px; font-size: 12px; line-height: 1.6; overflow-wrap: anywhere; }
.scheduled-scope { color: var(--text-muted); }
.scheduled-row { grid-template-columns: minmax(0, 1fr); }
.scheduled-row-actions { flex-wrap: wrap; }
.scheduled-row-main > span, .scheduled-row-main > small { white-space: normal; overflow: visible; text-overflow: clip; }
.scheduled-refresh { flex: 0 0 34px; }
.scheduled-row.has-server-block { grid-template-columns: minmax(0, 1fr); }
.scheduled-row.has-server-block .scheduled-row-actions { flex-wrap: wrap; }
.scheduled-editor > .inline-error { margin: 10px 13px 0; }
</style>
