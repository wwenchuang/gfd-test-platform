<script setup lang="ts">
import { computed } from 'vue'
import type { LoadAiAnalysis } from '../api/contracts'

const props = defineProps<{ analysis: LoadAiAnalysis | null; loading?: boolean; canCreateNext?: boolean }>()
const emit = defineEmits<{ reanalyze: []; createNext: [] }>()
const result = computed(() => props.analysis?.result || {})
const confidence = computed(() => {
  const value = result.value.confidence
  return value && typeof value === 'object' ? value as { level?: string; reason?: string } : null
})
const fallbackAdvice = computed(() => /回退|兜底/.test(confidence.value?.reason || ''))
function evidenceLabel(value: string): string {
  return ({ 'load.goal': '目标压力达成情况', 'latency.summary': '响应时间统计', 'transport.summary': 'HTTP 请求统计', 'workflow.summary': '完整链路统计' } as Record<string, string>)[value] || value
}
const recommendations = computed(() => Array.isArray(result.value.recommendations) ? result.value.recommendations as Array<Record<string, unknown>> : [])
const citations = computed(() => Array.isArray(result.value.evidence) ? result.value.evidence.map(String) : [])
const nextRun = computed(() => {
  const value = result.value.next_run
  return value && typeof value === 'object' ? value as Record<string, unknown> : null
})
const policy = computed(() => result.value.next_run_strategy as {source?: string; reason?: string; objective?: string; can_prefill?: boolean; limitations?: string[]; stop_conditions?: string[]; strategy_basis?: string} | undefined)
const stateLabel = computed(() => ({ queued: '等待诊断', running: '诊断中', completed: '诊断完成', failed: '诊断失败' } as Record<string, string>)[props.analysis?.state || ''] || '尚未诊断')
function categoryLabel(value: unknown): string {
  return ({
    no_bottleneck: '未发现明显瓶颈', target_service: '疑似目标服务瓶颈', network: '疑似网络瓶颈', load_agent: '疑似压测节点瓶颈',
    test_data: '疑似测试数据问题', mixed: '多因素混合', insufficient_evidence: '证据不足',
  } as Record<string, string>)[String(value || '')] || '尚未分类'
}
function priorityLabel(value: unknown): string { return ({ high: '高优先级', medium: '中优先级', low: '低优先级' } as Record<string, string>)[String(value || '')] || '建议' }
function modelLabel(value: unknown): string {
  return ({ 'constant-vus': '固定并发', 'ramping-vus': '阶梯并发', 'constant-arrival-rate': '固定吞吐', 'ramping-arrival-rate': '阶梯吞吐' } as Record<string, string>)[String(value || '')] || String(value || '')
}
</script>

<template>
  <section class="load-ai-panel" aria-label="AI性能诊断">
    <header><div><h2>AI 诊断</h2><p>AI 只解释已经固化的性能证据，不会重新执行压测。</p></div><button data-testid="load-reanalyze" class="secondary-command" type="button" :disabled="loading" @click="emit('reanalyze')">{{ loading ? '正在提交…' : analysis ? '重新诊断' : '开始诊断' }}</button></header>
    <p v-if="!analysis" class="compact-empty">确定性报告生成后会自动排队诊断，也可以手动重新诊断。</p>
    <template v-else>
      <div class="load-ai-meta"><span>{{ fallbackAdvice ? '平台规则建议' : stateLabel }}</span><span>模型：{{ analysis.model }}</span><span>提示词：{{ analysis.prompt_version }}</span><span>证据：{{ analysis.evidence_hash.slice(0, 12) }}</span></div>
      <p v-if="analysis.state === 'failed'" class="state-message state-error">{{ analysis.error || 'AI诊断失败，确定性报告仍然有效。' }}</p>
      <template v-else-if="analysis.state === 'completed'">
        <p v-if="fallbackAdvice" class="load-warning">当前展示平台规则建议，不是 AI 诊断结论。AI 回答未通过证据校验；上方压测结果和原始数据仍然有效。</p>
        <details v-if="fallbackAdvice"><summary>查看 AI 校验原因</summary>{{ confidence?.reason }}</details>
        <p v-else-if="confidence?.level === 'low'" class="load-warning">低置信度：{{ confidence.reason || '当前证据不足，请先补齐运行证据。' }}</p>
        <h3>{{ fallbackAdvice ? '平台建议结论' : '诊断结论' }}</h3><p><strong>{{ categoryLabel(result.bottleneck_category) }}</strong>：{{ result.conclusion }}</p>
        <h3>证据引用</h3><div class="load-evidence-tags"><code v-for="item in citations" :key="item" :title="item">{{ evidenceLabel(item) }}<small v-if="evidenceLabel(item) !== item">（{{ item }}）</small></code></div>
        <h3>处理建议</h3><ol class="load-recommendations"><li v-for="(item, index) in recommendations" :key="index"><b>{{ priorityLabel(item.priority) }}</b><strong>{{ item.action }}</strong><span>验证方式：{{ item.verification }}</span></li></ol>
        <template v-if="nextRun"><h3>下一轮怎么验证</h3><div v-if="policy" class="load-next-policy"><p><strong>{{ policy.source }}</strong></p><p><b>为什么这样建议：</b>{{ policy.reason }}</p><p><b>要验证什么：</b>{{ policy.objective }}</p><ul v-if="policy.limitations?.length"><li v-for="item in policy.limitations" :key="item">{{ item }}</li></ul><details><summary>停止条件与策略口径</summary><ul><li v-for="item in policy.stop_conditions" :key="item">{{ item }}</li></ul><p>{{ policy.strategy_basis }}</p><p>置信度表示当前诊断证据，不代表下一轮容量或安全保证。</p></details></div><p v-else class="load-warning">历史建议尚未按场景和服务监控策略校验，请重新诊断后使用。</p><p v-if="policy?.can_prefill" class="load-next-run"><strong>{{ modelLabel(nextRun.load_model) }}</strong> · 目标 {{ nextRun.target }} · {{ nextRun.duration_seconds }} 秒<br />{{ nextRun.agent_suggestion }}</p><button v-if="canCreateNext && policy?.can_prefill" data-testid="load-next-run" class="primary-command" type="button" @click="emit('createNext')">按建议配置下一轮 →</button><p v-if="canCreateNext && policy?.can_prefill" class="load-capacity-note">先查看配置差异、确认节点，再创建草稿。此操作不会立即发压。</p></template>
      </template>
    </template>
  </section>
</template>

<style scoped>
.load-next-policy{padding:14px 16px;border:1px solid #c9dedf;border-left:4px solid #128b83;border-radius:8px;background:#f4faf9;font-size:14px;line-height:1.7}.load-next-policy p{margin:4px 0 10px}.load-next-policy details{font-size:13px;color:#536778}.load-next-policy summary{cursor:pointer}.load-next-policy ul{padding-left:20px}
</style>
