<script setup lang="ts">
import { computed } from 'vue'
import { AlertTriangle, Lightbulb } from 'lucide-vue-next'

import type { ExecutionCaseResult } from '../api/contracts'
import { redactSensitiveEvidence } from '../utils/executionPresentation'

const props = defineProps<{ result: ExecutionCaseResult }>()
const detail = computed(() => props.result.sanitized_result)
const ai = computed(() => props.result.failure_analysis?.analysis || null)
const aiEvidence = computed(() => Array.isArray(ai.value?.evidence) ? ai.value!.evidence! : [])
const aiRecommendations = computed(() => Array.isArray(ai.value?.recommendations) ? ai.value!.recommendations! : [])
const errorMessage = computed(() => String(detail.value.error_message || ''))
const evidence = computed(() => {
  const assertions = Array.isArray(detail.value.assertion_results) ? detail.value.assertion_results : []
  if (assertions.length) return assertions.map(item => JSON.stringify(redactSensitiveEvidence(item))).slice(0, 3)
  if (errorMessage.value) return [errorMessage.value]
  return [`失败分类：${props.result.failure_category || 'unknown'}`]
})
const suggestion = computed(() => ({
  product_assertion: '对照实际响应与断言，确认是产品行为变化还是断言需要调整。',
  product_response: '检查服务端业务码和响应结构，确认接口是否返回了异常业务结果。',
  environment: '检查当前环境变量、服务地址和业务授权配置。',
  timeout: '检查目标服务可用性及响应时间，再决定是否调整超时。',
  network: '检查网络连通性、DNS 和目标服务状态。',
  parser: '检查响应内容类型与 JSON 结构。',
}[props.result.failure_category] || '先根据请求、响应和日志证据定位，再修改用例或服务。'))
</script>

<template>
  <section v-if="result.status !== 'PASSED'" class="failure-analysis">
    <template v-if="ai">
      <header><AlertTriangle :size="16" /><strong>AI 失败分析</strong><span>{{ result.failure_analysis?.model }}</span></header>
      <p><strong>{{ ai.summary }}</strong></p>
      <p>{{ ai.root_cause }}</p>
      <ul><li v-for="item in aiEvidence" :key="item">{{ item }}</li></ul>
      <p v-for="item in aiRecommendations" :key="item"><Lightbulb :size="15" />{{ item }}</p>
    </template>
    <template v-else>
      <header><AlertTriangle :size="16" /><strong>平台诊断</strong><span>规则提示</span></header>
      <ul><li v-for="item in evidence" :key="item">{{ item }}</li></ul>
      <p><Lightbulb :size="15" />{{ suggestion }}</p>
    </template>
  </section>
</template>
