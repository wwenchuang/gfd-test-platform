<script setup lang="ts">
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { CalendarClock, Check, Play, RefreshCw, Save } from 'lucide-vue-next'

import type { ApiBaselineCase, ApiTestTask, CaseVersion, ScheduledJob } from '../api/contracts'
import { baselineGroup, useBaselinesStore } from '../stores/baselines'
import { useCasesStore } from '../stores/cases'
import { useContextStore } from '../stores/context'
import { useScheduledJobsStore } from '../stores/scheduledJobs'
import { useTasksStore } from '../stores/tasks'

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

interface TargetOption {
  id: string
  title: string
  subtitle: string
  meta: string
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
const activeBaselines = computed(() => baselines.items.filter(item => item.status === 'active'))
const targetOptions = computed<TargetOption[]>(() => {
  if (form.targetType === 'baseline_group') return baselineGroupOptions()
  if (form.targetType === 'baselines') return activeBaselines.value.map(baselineOption)
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

onMounted(async () => {
  await Promise.all([context.loadSavedContext(), context.loadOptions()])
  await Promise.all([
    projectId.value ? scheduledJobs.load(projectId.value) : Promise.resolve(),
    loadTargetAssets(),
  ])
})

watch(() => form.targetType, () => {
  selectedTargetIds.value = []
  targetSearch.value = ''
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
  await scheduledJobs.create({
    project_id: projectId.value,
    source_revision_id: sourceRevisionId.value,
    environment_revision_id: environmentRevisionId.value,
    environment_id: form.environmentStrategy === 'latest_environment' ? environmentId.value : undefined,
    name: form.name.trim(),
    target_type: form.targetType,
    target_ids: ids,
    schedule_type: form.scheduleType,
    cron_expression: form.scheduleType === 'cron' ? form.cronExpression.trim() : '',
    environment_strategy: form.environmentStrategy,
    enabled: form.enabled,
    notify_feishu: form.notifyFeishu,
    retry_count: form.retryCount,
    timeout_seconds: form.timeoutSeconds,
  })
}

async function runJob(job: ScheduledJob): Promise<void> {
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

function setScheduleType(type: ScheduledJob['schedule_type']): void {
  form.scheduleType = type
  if (type === 'cron' && !form.cronExpression.trim()) applyCronPreset(cronPresets[0].expression)
}

function applyCronPreset(expression: string): void {
  form.cronExpression = expression
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

function scheduleLabel(job: ScheduledJob): string {
  if (job.schedule_type === 'cron') return job.cron_expression || 'Cron'
  return job.schedule_type === 'weekly' ? '每周' : '每天'
}

function baselineGroupOptions(): TargetOption[] {
  const counts = new Map<string, number>()
  for (const item of activeBaselines.value) {
    const name = baselineGroup(item)
    counts.set(name, (counts.get(name) || 0) + 1)
  }
  return [...counts.entries()]
    .sort(([left], [right]) => left.localeCompare(right, 'zh-CN'))
    .map(([name, count]) => ({
      id: name,
      title: name,
      subtitle: `${count} 条可执行基线`,
      meta: '基线分组',
    }))
}

function baselineOption(item: ApiBaselineCase): TargetOption {
  return {
    id: item.id,
    title: item.case_name || item.endpoint_summary || item.path,
    subtitle: `${item.method} ${item.path}`,
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
</script>

<template>
  <section class="workspace scheduled-workspace">
    <header class="page-toolbar">
      <div>
        <p class="eyebrow">API SCHEDULED JOBS</p>
        <h1>定时任务</h1>
        <p class="page-subtitle">定时任务独立保存项目、目标和环境策略；手动执行会生成带“定时任务”来源的执行记录。</p>
      </div>
      <button type="button" class="secondary-command" :disabled="!projectId || scheduledJobs.loading" @click="scheduledJobs.load(projectId)">
        <RefreshCw :size="15" :class="{ 'is-spinning': scheduledJobs.loading }" />刷新
      </button>
    </header>

    <p v-if="scheduledJobs.error" class="inline-error">{{ scheduledJobs.error }}</p>

    <div class="scheduled-layout">
      <section class="scheduled-list">
        <header class="panel-header"><h2>任务列表</h2><span>{{ scheduledJobs.items.length }}</span></header>
        <article v-for="job in scheduledJobs.items" :key="job.id" class="scheduled-row">
          <div>
            <strong>{{ job.name }}</strong>
            <span>{{ targetTypeLabel(job.target_type) }} · {{ scheduleLabel(job) }} · {{ job.notify_feishu ? '飞书通知' : '不通知' }}</span>
            <small>{{ job.target_ids.join('、') || '暂无目标' }}</small>
          </div>
          <button :data-testid="`scheduled-run-${job.id}`" type="button" class="secondary-command" :disabled="scheduledJobs.runningId === job.id" @click="runJob(job)">
            <Play :size="14" />{{ scheduledJobs.runningId === job.id ? '投递中' : '手动执行一次' }}
          </button>
        </article>
        <p v-if="!scheduledJobs.items.length" class="section-empty">暂无定时任务。</p>
      </section>

      <section class="scheduled-editor">
        <header class="panel-header"><h2>新建定时任务</h2><CalendarClock :size="17" /></header>
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
          </fieldset>
          <section v-if="form.scheduleType === 'cron'" class="cron-preset-panel wide">
            <label>Cron 表达式<input v-model="form.cronExpression" data-testid="scheduled-cron" placeholder="0 2 * * *" /></label>
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
          <button data-testid="scheduled-save" type="button" class="primary-command" :disabled="scheduledJobs.saving" @click="saveJob"><Save :size="14" />{{ scheduledJobs.saving ? '保存中' : '保存定时任务' }}</button>
        </footer>
      </section>
    </div>
  </section>
</template>
