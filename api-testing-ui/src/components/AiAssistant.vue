<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { Bot, ChevronLeft, ChevronRight, Clock3, FilePlus2, ListChecks, RefreshCw, Sparkles } from 'lucide-vue-next'

import type { AiJob, CaseVersion } from '../api/contracts'

const props = withDefaults(defineProps<{
  selectedCount: number
  job: AiJob | null
  generatedCases?: CaseVersion[]
  error?: string
  polling?: boolean
  canResume?: boolean
  basicGenerating?: boolean
}>(), {
  generatedCases: () => [], error: '', polling: false, canResume: false, basicGenerating: false,
})
const emit = defineEmits<{
  generate: [intent: string]
  retry: [intent: string]
  resume: []
  'generate-basic': []
  'open-generated': [version: CaseVersion]
  'manage-generated': []
}>()
const collapsed = ref(false)
const intent = ref('覆盖正常流程、请求体字段边界、参数边界与业务失败响应；Biz、Authorization、ZXBToken 由环境自动注入，不生成请求头或鉴权类用例')
const elapsedSeconds = ref(0)
let elapsedTimer: ReturnType<typeof setInterval> | null = null
const STATE_LABELS: Record<string, string> = { queued: '排队中', running: '生成中', completed: '已完成', partial: '部分完成', failed: '失败', failed_gateway: '模型调用失败', failed_validation: '校验失败' }
const stateLabel = computed(() => STATE_LABELS[props.job?.state || ''] || '等待生成')
const running = computed(() => props.polling)
const elapsedLabel = computed(() => elapsedSeconds.value < 60
  ? `${elapsedSeconds.value} 秒`
  : `${Math.floor(elapsedSeconds.value / 60)} 分 ${elapsedSeconds.value % 60} 秒`)

watch(() => props.polling, active => {
  if (elapsedTimer) clearInterval(elapsedTimer)
  elapsedTimer = null
  if (!active) return
  elapsedSeconds.value = 0
  elapsedTimer = setInterval(() => { elapsedSeconds.value += 1 }, 1000)
}, { immediate: true })
onBeforeUnmount(() => { if (elapsedTimer) clearInterval(elapsedTimer) })

function useIntent(value: string): void { intent.value = value }
function validationMessage(batch: AiJob['batches'][number]): string {
  const issue = batch.validation_errors.find(item => typeof item.message === 'string')
  if (issue?.code === 'missing_endpoint_coverage') return '部分已选接口没有生成有效用例，请检查草稿后重试缺口'
  const message = issue?.message
  if (message === 'AI Gateway content is not strict JSON') return 'AI 返回内容格式不正确，请重新生成'
  return typeof message === 'string' ? message : ''
}
</script>

<template>
  <aside :class="['ai-assistant', { collapsed }]">
    <header class="panel-header"><div><Bot :size="17" /><h2 v-if="!collapsed">AI 助手</h2></div><button class="mini-icon" type="button" :title="collapsed ? '展开 AI 助手' : '收起 AI 助手'" @click="collapsed = !collapsed"><ChevronLeft v-if="!collapsed" :size="16" /><ChevronRight v-else :size="16" /></button></header>
    <template v-if="!collapsed">
      <div class="assistant-body">
        <p class="assistant-lead">已选择 {{ selectedCount }} 个接口。AI 负责设计候选，平台负责校验与执行。</p>
        <label>测试意图<textarea v-model="intent" rows="4" /></label>
        <div class="assistant-template-label">意图模板 <small>只填充上方内容，不会立即生成</small></div>
        <div class="assistant-actions"><button type="button" @click="useIntent('先分析接口合同、Body 示例、Query/Path 参数和业务风险，再生成正常、边界与业务失败用例；不要生成请求头或鉴权类用例')">接口合同与风险</button><button type="button" @click="useIntent('重点补充状态码、业务码、响应结构和关键字段断言')">业务断言</button><button type="button" @click="useIntent('根据最近失败分类生成可复现用例，并区分产品失败与脚本问题')">失败复现</button></div>
        <button data-testid="generate-basic-positive" class="secondary-command wide" type="button" :disabled="!selectedCount || running || basicGenerating" @click="emit('generate-basic')"><FilePlus2 :size="16" />{{ basicGenerating ? '正在生成基础用例' : '生成基础正向用例' }}</button>
        <button class="primary-command wide" type="button" :disabled="!selectedCount || running || basicGenerating" @click="emit('generate', intent)"><Sparkles :size="16" />{{ running ? 'AI 正在生成' : '生成测试用例' }}</button>
        <p v-if="running" class="ai-progress-note"><Clock3 :size="14" />已等待 {{ elapsedLabel }}，生成会在后台继续，可稍后返回查看。</p>
        <p v-if="error" class="state-message" :class="{ 'state-error': !canResume }">{{ error }}</p>
        <section v-if="job" class="ai-job" aria-live="polite">
          <div class="job-summary"><strong>{{ stateLabel }}</strong><span>{{ job.actual_model || job.requested_model || '由平台选择模型' }}</span></div>
          <article v-for="batch in job.batches" :key="batch.id" class="batch-row"><span>批次 {{ batch.sequence }}</span><strong>{{ ({ queued: '排队', running: '生成中', completed: '完成', partial: '部分完成', failed: '失败', failed_gateway: '模型失败', failed_validation: '校验失败' } as Record<string,string>)[batch.state] || batch.state }}</strong><small>{{ batch.actual_model || batch.requested_model || '等待模型' }} · {{ batch.generated_draft_ids.length }} 个草稿</small><small v-if="validationMessage(batch)" class="batch-error" role="alert">{{ validationMessage(batch) }}</small></article>
          <button v-if="canResume" class="secondary-command" type="button" @click="emit('resume')"><RefreshCw :size="15" />继续查看</button>
          <button v-else-if="['partial','failed','failed_gateway','failed_validation'].includes(job.state)" class="secondary-command" type="button" @click="emit('retry', intent)"><RefreshCw :size="15" />重新生成当前范围</button>
        </section>
        <section v-if="generatedCases.length" class="ai-generated-list">
          <header><strong>已生成用例 {{ generatedCases.length }}</strong><button type="button" class="text-command" @click="emit('manage-generated')"><ListChecks :size="13" />统一管理</button></header>
          <button v-for="version in generatedCases" :key="version.id" :data-testid="`ai-generated-open-${version.id}`" type="button" class="ai-generated-row" @click="emit('open-generated', version)"><span><strong>{{ version.name }}</strong><small>{{ version.request.method }} {{ version.request.path }}</small></span><b>v{{ version.version || 1 }}</b></button>
        </section>
      </div>
    </template>
  </aside>
</template>
