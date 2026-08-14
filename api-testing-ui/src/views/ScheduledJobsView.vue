<script setup lang="ts">
import { computed, onMounted, reactive } from 'vue'
import { useRouter } from 'vue-router'
import { CalendarClock, Play, RefreshCw, Save } from 'lucide-vue-next'

import type { ScheduledJob } from '../api/contracts'
import { useContextStore } from '../stores/context'
import { useScheduledJobsStore } from '../stores/scheduledJobs'

const context = useContextStore()
const scheduledJobs = useScheduledJobsStore()
const router = useRouter()

const form = reactive({
  name: '',
  targetType: 'baseline_group' as ScheduledJob['target_type'],
  targetIdsText: '',
  scheduleType: 'daily' as ScheduledJob['schedule_type'],
  cronExpression: '',
  environmentStrategy: 'fixed_revision' as ScheduledJob['environment_strategy'],
  enabled: true,
  notifyFeishu: false,
  retryCount: 0,
  timeoutSeconds: 1800,
})

const projectId = computed(() => context.projectId || context.projects[0]?.id || '')
const sourceRevisionId = computed(() => context.sourceRevisionId || context.sourceRevisions.find(item => item.project_id === projectId.value)?.id || '')
const environmentRevisionId = computed(() => context.environmentRevisionId || context.environmentRevisions.find(item => item.project_id === projectId.value)?.id || '')
const environmentId = computed(() => context.environmentRevisions.find(item => item.id === environmentRevisionId.value)?.environment_id || '')
const targetLabel = computed(() => ({
  cases: '用例版本 ID',
  task: '已保存任务 ID',
  baselines: '基线 ID',
  baseline_group: '基线分组名',
}[form.targetType]))

onMounted(async () => {
  await Promise.all([context.loadSavedContext(), context.loadOptions()])
  if (projectId.value) await scheduledJobs.load(projectId.value)
})

function targetIds(): string[] {
  return form.targetIdsText
    .split(/[\n,，]/)
    .map(item => item.trim())
    .filter(Boolean)
}

async function saveJob(): Promise<void> {
  if (!projectId.value || !sourceRevisionId.value || !environmentRevisionId.value) {
    scheduledJobs.error = '请先选择项目、接口版本和执行环境'
    return
  }
  await scheduledJobs.create({
    project_id: projectId.value,
    source_revision_id: sourceRevisionId.value,
    environment_revision_id: environmentRevisionId.value,
    environment_id: form.environmentStrategy === 'latest_environment' ? environmentId.value : undefined,
    name: form.name.trim(),
    target_type: form.targetType,
    target_ids: targetIds(),
    schedule_type: form.scheduleType,
    cron_expression: form.cronExpression.trim(),
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
          <label class="wide">{{ targetLabel }}<textarea v-model="form.targetIdsText" data-testid="scheduled-targets" rows="4" placeholder="多项可用换行或逗号分隔" /></label>
          <label>周期
            <select v-model="form.scheduleType">
              <option value="daily">每天</option>
              <option value="weekly">每周</option>
              <option value="cron">Cron</option>
            </select>
          </label>
          <label>Cron 表达式<input v-model="form.cronExpression" :disabled="form.scheduleType !== 'cron'" placeholder="0 2 * * *" /></label>
          <label>环境策略
            <select v-model="form.environmentStrategy">
              <option value="fixed_revision">固定当前环境版本</option>
              <option value="latest_environment">执行时取环境最新版本</option>
            </select>
          </label>
          <label>失败重试<input v-model.number="form.retryCount" type="number" min="0" max="5" /></label>
          <label>超时秒数<input v-model.number="form.timeoutSeconds" type="number" min="30" max="86400" /></label>
          <label class="toggle-card"><input v-model="form.enabled" type="checkbox" />启用</label>
          <label class="toggle-card"><input v-model="form.notifyFeishu" data-testid="scheduled-notify" type="checkbox" />飞书通知</label>
        </div>
        <footer class="notification-actions">
          <span>当前项目：{{ context.projects.find(item => item.id === projectId)?.name || '未选择' }}</span>
          <button data-testid="scheduled-save" type="button" class="primary-command" :disabled="scheduledJobs.saving" @click="saveJob"><Save :size="14" />{{ scheduledJobs.saving ? '保存中' : '保存定时任务' }}</button>
        </footer>
      </section>
    </div>
  </section>
</template>
