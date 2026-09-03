<script setup lang="ts">
import { computed } from 'vue'
import type { LoadAiAnalysis } from '../api/contracts'

const props = defineProps<{ analysis: LoadAiAnalysis | null; loading?: boolean }>()
const emit = defineEmits<{ reanalyze: [] }>()
const result = computed(() => props.analysis?.result || {})
const confidence = computed(() => {
  const value = result.value.confidence
  return value && typeof value === 'object' ? value as { level?: string; reason?: string } : null
})
const recommendations = computed(() => Array.isArray(result.value.recommendations) ? result.value.recommendations as Array<Record<string, unknown>> : [])
const citations = computed(() => Array.isArray(result.value.evidence) ? result.value.evidence.map(String) : [])
const nextRun = computed(() => {
  const value = result.value.next_run
  return value && typeof value === 'object' ? value as Record<string, unknown> : null
})
const stateLabel = computed(() => ({ queued: '等待诊断', running: '诊断中', completed: '诊断完成', failed: '诊断失败' } as Record<string, string>)[props.analysis?.state || ''] || '尚未诊断')
function categoryLabel(value: unknown): string {
  return ({
    target_service: '疑似目标服务瓶颈', network: '疑似网络瓶颈', load_agent: '疑似压测节点瓶颈',
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
    <header><div><h2>AI 诊断</h2><p>AI 只解释已经固化的性能证据，不会重新执行压测。</p></div><button data-testid="load-reanalyze" class="secondary-command" type="button" :disabled="loading" @click="emit('reanalyze')">{{ loading ? '正在提交…' : '重新诊断' }}</button></header>
    <p v-if="!analysis" class="compact-empty">确定性报告生成后会自动排队诊断，也可以手动重新诊断。</p>
    <template v-else>
      <div class="load-ai-meta"><span>{{ stateLabel }}</span><span>模型：{{ analysis.model }}</span><span>提示词：{{ analysis.prompt_version }}</span><span>证据：{{ analysis.evidence_hash.slice(0, 12) }}</span></div>
      <p v-if="analysis.state === 'failed'" class="state-message state-error">{{ analysis.error || 'AI诊断失败，确定性报告仍然有效。' }}</p>
      <template v-else-if="analysis.state === 'completed'">
        <p v-if="confidence?.level === 'low'" class="load-warning">低置信度：{{ confidence.reason || '当前证据不足，请先补齐运行证据。' }}</p>
        <h3>诊断结论</h3><p><strong>{{ categoryLabel(result.bottleneck_category) }}</strong>：{{ result.conclusion }}</p>
        <h3>证据引用</h3><div class="load-evidence-tags"><code v-for="item in citations" :key="item">{{ item }}</code></div>
        <h3>处理建议</h3><ol class="load-recommendations"><li v-for="(item, index) in recommendations" :key="index"><b>{{ priorityLabel(item.priority) }}</b><strong>{{ item.action }}</strong><span>验证方式：{{ item.verification }}</span></li></ol>
        <template v-if="nextRun"><h3>下一轮怎么验证</h3><p class="load-next-run"><strong>{{ modelLabel(nextRun.load_model) }}</strong> · 目标 {{ nextRun.target }} · {{ nextRun.duration_seconds }} 秒<br />{{ nextRun.agent_suggestion }}</p></template>
      </template>
    </template>
  </section>
</template>
