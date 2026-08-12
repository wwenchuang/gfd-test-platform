<script setup lang="ts">
import { computed } from 'vue'

import type { ExecutionView } from '../api/contracts'
import { executionConclusion, executionMetrics, executionTypeLabel, formatDuration } from '../utils/executionPresentation'

const props = defineProps<{ execution: ExecutionView }>()
const metrics = computed(() => executionMetrics(props.execution))
const conclusion = computed(() => executionConclusion(props.execution))
</script>

<template>
  <section class="execution-overview">
    <div class="overview-identity">
      <span>任务</span>
      <strong>{{ executionTypeLabel(execution) }}</strong>
      <code>{{ execution.id }}</code>
    </div>
    <div class="overview-environment"><span>执行环境</span><strong>{{ execution.environment_name || '未命名环境' }}</strong></div>
    <div class="overview-conclusion"><span>本次结论</span><strong data-testid="execution-conclusion" :class="`tone-${conclusion.tone}`">{{ conclusion.label }}</strong></div>
    <div class="overview-metric"><span>总用例</span><strong>{{ metrics.total }}</strong></div>
    <div class="overview-metric" data-testid="overview-passed"><span>通过</span><strong class="status-passed">{{ metrics.passed }}</strong></div>
    <div class="overview-metric" data-testid="overview-failed"><span>失败</span><strong class="status-failed">{{ metrics.failed }}</strong></div>
    <div class="overview-metric" data-testid="overview-broken"><span>异常</span><strong class="status-broken">{{ metrics.broken }}</strong></div>
    <div class="overview-metric" data-testid="overview-skipped"><span>跳过</span><strong>{{ metrics.skipped }}</strong></div>
    <div class="overview-metric" data-testid="overview-rate"><span>通过率</span><strong>{{ metrics.passRate }}%</strong></div>
    <div class="overview-metric" data-testid="overview-duration"><span>总耗时</span><strong>{{ formatDuration(metrics.durationMs) }}</strong></div>
  </section>
</template>
