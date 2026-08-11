<script setup lang="ts">
import { computed, onMounted } from 'vue'
import { useRouter } from 'vue-router'

import ExecutionDetailDrawer from '../components/ExecutionDetailDrawer.vue'
import ReportSummary from '../components/ReportSummary.vue'
import type { ExecutionCaseResult, ExecutionView } from '../api/contracts'
import { useContextStore } from '../stores/context'
import { useExecutionsStore } from '../stores/executions'

const context = useContextStore()
const executions = useExecutionsStore()
const router = useRouter()
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
  <section class="workspace"><header class="page-toolbar"><div><p class="eyebrow">API TEST REPORTS</p><h1>测试报告</h1><p class="page-subtitle">产品断言失败与脚本、环境异常分开统计。</p></div></header><div class="report-list"><button v-for="report in reports" :key="report.id" type="button" @click="executions.active = report"><div><strong>{{ report.execution_type === 'debug' ? '在线调试' : '自动回归' }}</strong><span>{{ report.environment_name }} · {{ new Date(report.created_at).toLocaleString('zh-CN') }}</span></div><ReportSummary :summary="report.summary" :duration-ms="report.case_results.reduce((total, item) => total + item.duration_ms, 0)" :environment-name="report.environment_name" /></button><div v-if="!reports.length" class="section-empty">暂无已完成报告。</div></div><ExecutionDetailDrawer v-if="executions.active" :execution="executions.active" @close="executions.active = null" @edit="edit" @rerun="executions.rerunFailed($event)" /></section>
</template>
