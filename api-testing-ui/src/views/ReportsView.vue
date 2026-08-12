<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'

import DiagnosticReport from '../components/DiagnosticReport.vue'
import ReportSummary from '../components/ReportSummary.vue'
import type { ExecutionCaseResult, ExecutionView } from '../api/contracts'
import { executionConclusion, executionMetrics } from '../utils/executionPresentation'
import { useContextStore } from '../stores/context'
import { useExecutionsStore } from '../stores/executions'

const context = useContextStore()
const executions = useExecutionsStore()
const router = useRouter()
const selected = ref<ExecutionView | null>(null)
const reports = computed(() => executions.executions.filter(item => ['DONE', 'CANCELLED'].includes(item.state)))
onMounted(async () => {
  await Promise.all([context.loadSavedContext(), context.loadOptions()])
  if (context.projectId) await executions.load(context.projectId)
})

function edit(result: ExecutionCaseResult, execution: ExecutionView): void {
  void router.push({ name: 'workbench', query: {
    endpointId: result.endpoint_id, caseVersionId: result.case_version_id,
    projectId: execution.project_id, sourceRevisionId: execution.source_revision_id,
    environmentRevisionId: execution.environment_revision_id,
  } })
}
</script>

<template>
  <section class="workspace">
    <template v-if="selected"><DiagnosticReport :execution="selected" @back="selected = null" @edit="edit" @rerun="executions.rerunFailed($event)" /></template>
    <template v-else><header class="page-toolbar"><div><p class="eyebrow">API TEST REPORTS</p><h1>测试报告</h1><p class="page-subtitle">先看结论和问题分布，再按用例展开真实证据。</p></div></header><div class="report-list"><button v-for="report in reports" :key="report.id" data-testid="report-history-row" type="button" @click="selected = report"><div><strong>{{ report.execution_type === 'debug' ? '在线调试' : '自动回归' }}</strong><span>{{ new Date(report.created_at).toLocaleString('zh-CN') }}</span></div><ReportSummary compact :summary="executionMetrics(report)" :duration-ms="executionMetrics(report).durationMs" :environment-name="report.environment_name" :conclusion="executionConclusion(report).label" /></button><div v-if="!reports.length" class="section-empty">暂无已完成报告。</div></div></template>
  </section>
</template>
