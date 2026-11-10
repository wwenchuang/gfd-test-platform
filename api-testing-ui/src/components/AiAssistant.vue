<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { Bot, ChevronLeft, ChevronRight, Clock3, FilePlus2, ListChecks, RefreshCw, Sparkles } from 'lucide-vue-next'

import type { AiJob, CaseVersion } from '../api/contracts'
import { presentAiValidationIssue } from '../utils/aiValidationPresentation'

const props = withDefaults(defineProps<{
  selectedCount: number
  job: AiJob | null
  generatedCases?: CaseVersion[]
  historicalScopeMessage?: string
  error?: string
  polling?: boolean
  preparing?: boolean
  canResume?: boolean
  basicGenerating?: boolean
  diagnosingBatchId?: string
  scopeKey?: string
}>(), {
  generatedCases: () => [], historicalScopeMessage: '', error: '', polling: false, preparing: false, canResume: false, basicGenerating: false, diagnosingBatchId: '', scopeKey: '',
})
const emit = defineEmits<{
  generate: [intent: string, thinkingMode?: 'off']
  retry: [intent: string, thinkingMode?: 'off']
  resume: []
  'generate-basic': []
  'open-generated': [version: CaseVersion]
  'manage-generated': []
  'diagnose-validation': [batchId: string, errorIndex: number]
}>()
const collapsed = ref(false)
const intent = ref('覆盖正常流程、请求体字段边界、参数边界与业务失败响应；Biz、Authorization、ZXBToken 由环境自动注入，不生成请求头或鉴权类用例')
const thinkingMode = ref<'default' | 'off'>('default')
const modeError = ref('')
function sessionIdentity(): string | null {
  try { return globalThis.sessionStorage?.getItem('sessionToken') || null } catch { return null }
}
let thinkingSession = sessionIdentity()
let resetModeWhenIdle = false
function rememberThinkingSession(): void { thinkingSession = sessionIdentity(); modeError.value = '' }
function resetThinkingMode(): void { thinkingMode.value = 'default'; modeError.value = ''; thinkingSession = sessionIdentity(); resetModeWhenIdle = false }
watch(() => props.scopeKey, () => { if (props.polling || props.preparing) resetModeWhenIdle = true; else resetThinkingMode() }, { flush: 'sync' })
watch(() => props.polling || props.preparing, active => { if (!active && resetModeWhenIdle) resetThinkingMode() }, { flush: 'sync' })
function generate(event: 'generate' | 'retry'): void {
  if (thinkingMode.value === 'off' && thinkingSession !== sessionIdentity()) {
    thinkingMode.value = 'default'
    modeError.value = '登录状态已变化，请重新选择模型设置。'
    return
  }
  modeError.value = ''
  if (event === 'generate') {
    if (thinkingMode.value === 'off') emit('generate', intent.value, 'off')
    else emit('generate', intent.value)
  } else {
    if (thinkingMode.value === 'off') emit('retry', intent.value, 'off')
    else emit('retry', intent.value)
  }
}
function gatewayMessage(code?: string): { title: string; detail: string } {
  if (code === 'enqueue_failed') return { title: '任务提交失败', detail: '任务未能进入执行队列，本批次未发起模型调用。可以稍后重新生成；如果持续失败，请联系管理员检查任务队列。' }
  if (code === 'thinking_route_unsupported') return { title: '当前模型渠道不支持关闭思考', detail: '本批次未发起模型调用。请恢复默认设置，或联系管理员检查当前模型渠道。' }
  if (code === 'thinking_mode_unconfirmed') return { title: '无法确认本次模型设置', detail: '该批次没有保存新草稿，模型调用可能已产生费用。请联系管理员检查网关版本。' }
  if (code === 'compatible_worker_timeout') return { title: '等待兼容执行器超时', detail: '本批次未发起模型调用。请联系管理员检查执行器版本和队列状态。' }
  if (code === 'generation_uncertain') return { title: '本批次生成结果不确定', detail: '模型调用可能产生费用，请先核对任务和草稿，再决定是否重试。' }
  return { title: code === 'gateway_timeout' ? '模型服务或连接等待超时' : '模型服务调用失败', detail: '本次模型调用没有生成新草稿，已保存的草稿仍可查看。可以稍后重新生成；如果持续失败，请联系管理员检查模型服务。' }
}
const elapsedSeconds = ref(0)
let elapsedTimer: ReturnType<typeof setInterval> | null = null
const STATE_LABELS: Record<string, string> = { queued: '排队中', running: '生成中', completed: '已完成', partial: '部分完成', failed: '失败', failed_gateway: '模型调用失败', failed_validation: '校验失败' }
const stateLabel = computed(() => {
  if (props.job?.state === 'failed_gateway' && props.job.batches.some(batch => batch.error_code === 'generation_uncertain')) return '生成结果不确定'
  if (props.job?.state === 'failed_gateway' && props.job.batches.length > 0 && props.job.batches.every(batch => batch.error_code === 'compatible_worker_timeout')) return '等待兼容执行器超时'
  if (props.job?.state === 'failed_gateway' && props.job.batches.length > 0 && props.job.batches.every(batch => batch.error_code === 'thinking_route_unsupported')) return '模型设置不受支持'
  return props.job?.state === 'failed_gateway' && props.job.batches.some(batch => batch.error_code === 'enqueue_failed') ? '任务提交失败' : STATE_LABELS[props.job?.state || ''] || '等待生成'
})
const running = computed(() => props.polling || props.preparing)
const pendingJob = computed(() => props.canResume && ['queued', 'running'].includes(props.job?.state || ''))
const generateLabel = computed(() => props.preparing ? '正在准备任务'
  : props.polling && props.job?.state === 'queued' ? '任务排队中'
    : props.polling && props.job?.state === 'running' ? 'AI 正在生成'
      : props.polling ? '正在查询任务状态' : '生成测试用例')
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
function validationIssue(batch: AiJob['batches'][number]) {
  return presentAiValidationIssue(batch.validation_errors)
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
        <details data-testid="ai-advanced">
          <summary>模型高级设置</summary>
          <label>思考模式<select v-model="thinkingMode" data-testid="ai-thinking-mode" :disabled="running || basicGenerating" @change="rememberThinkingSession"><option value="default">默认（推荐）</option><option value="off">关闭思考（用于排查超时）</option></select></label>
          <small>关闭思考仅适用于官方 Qwen3.8-Max，不使用备用模型，也不保证生成更快。</small>
        </details>
        <p class="ai-progress-note">AI 生成可能产生费用，失败也可能产生费用。</p>
        <p v-if="modeError" class="state-message state-error" role="alert">{{ modeError }}</p>
        <button data-testid="generate-ai" class="primary-command wide" type="button" :disabled="!selectedCount || running || pendingJob || basicGenerating" @click="generate('generate')"><Sparkles :size="16" />{{ generateLabel }}</button>
        <p v-if="preparing" class="ai-progress-note"><Clock3 :size="14" />正在准备并提交任务，请稍候。</p>
        <p v-else-if="polling && job?.state === 'queued'" class="ai-progress-note"><Clock3 :size="14" />最近状态显示任务仍在排队，尚未确认执行器接收；无兼容执行器时可能持续排队，请联系管理员检查。</p>
        <p v-else-if="polling && job?.state === 'running'" class="ai-progress-note"><Clock3 :size="14" />已等待 {{ elapsedLabel }}，最近状态显示任务生成中，可稍后查看最新进度。</p>
        <p v-else-if="polling" class="ai-progress-note"><Clock3 :size="14" />正在查询任务状态，请稍候。</p>
        <p v-if="error" class="state-message" :class="{ 'state-error': !canResume }">{{ error }}</p>
        <section v-if="historicalScopeMessage" class="ai-historical-scope" role="status">
          <strong>当前接口范围暂无对应生成结果</strong>
          <p>{{ historicalScopeMessage }}</p>
          <button data-testid="manage-historical-ai-cases" type="button" class="text-command" @click="emit('manage-generated')"><ListChecks :size="13" />查看历史用例</button>
        </section>
        <section v-if="job" class="ai-job" aria-live="polite">
          <div class="job-summary"><strong>{{ stateLabel }}</strong><span>{{ job.actual_model || job.requested_model || '由平台选择模型' }}</span></div>
          <article v-for="batch in job.batches" :key="batch.id" class="batch-row"><span>批次 {{ batch.sequence }}</span><strong>{{ batch.error_code === 'enqueue_failed' ? '任务提交失败' : batch.error_code === 'thinking_route_unsupported' ? '设置不受支持' : batch.error_code === 'compatible_worker_timeout' ? '等待执行器超时' : batch.error_code === 'generation_uncertain' ? '结果不确定' : ({ queued: '排队', running: '生成中', completed: '完成', partial: '部分完成', failed: '失败', failed_gateway: '模型失败', failed_validation: '校验失败' } as Record<string,string>)[batch.state] || batch.state }}</strong><small>{{ batch.actual_model || batch.requested_model || '等待模型' }} · {{ batch.generated_draft_ids.length }} 个草稿</small><section v-if="batch.state === 'failed_gateway'" class="batch-diagnostic" role="alert"><strong>{{ gatewayMessage(batch.error_code).title }}</strong><p>{{ gatewayMessage(batch.error_code).detail }}</p></section><section v-if="validationIssue(batch)" class="batch-diagnostic" role="alert"><small v-if="validationIssue(batch)?.model" class="diagnosis-model">千问分析 · {{ validationIssue(batch)?.model }}</small><strong>{{ validationIssue(batch)?.title }}</strong><p>{{ validationIssue(batch)?.reason }}</p><ol v-if="validationIssue(batch)?.actions.length"><li v-for="action in validationIssue(batch)?.actions" :key="action">{{ action }}</li></ol><button v-if="validationIssue(batch)?.needsAiDiagnosis" :data-testid="`diagnose-validation-${batch.id}`" class="secondary-command validation-diagnose-command" type="button" :disabled="Boolean(diagnosingBatchId)" @click="emit('diagnose-validation', batch.id, validationIssue(batch)?.issueIndex || 0)"><Sparkles :size="14" />{{ diagnosingBatchId === batch.id ? '千问正在分析' : '使用当前配置的千问分析' }}</button><details v-if="validationIssue(batch)?.originalMessage"><summary>查看英文原文</summary><code data-testid="validation-original-message">{{ validationIssue(batch)?.originalMessage }}</code></details></section></article>
          <button v-if="canResume" class="secondary-command" type="button" @click="emit('resume')"><RefreshCw :size="15" />继续查看</button>
          <button v-else-if="['partial','failed','failed_gateway','failed_validation'].includes(job.state)" class="secondary-command" type="button" @click="generate('retry')"><RefreshCw :size="15" />重新生成当前范围</button>
        </section>
        <section v-if="generatedCases.length" class="ai-generated-list">
          <header><strong>已生成用例 {{ generatedCases.length }}</strong><button type="button" class="text-command" @click="emit('manage-generated')"><ListChecks :size="13" />统一管理</button></header>
          <button v-for="version in generatedCases" :key="version.id" :data-testid="`ai-generated-open-${version.id}`" type="button" class="ai-generated-row" @click="emit('open-generated', version)"><span><strong>{{ version.name }}</strong><small>{{ version.request.method }} {{ version.request.path }}</small></span><b>v{{ version.version || 1 }}</b></button>
        </section>
      </div>
    </template>
  </aside>
</template>
