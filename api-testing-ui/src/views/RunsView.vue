<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import ExecutionConsole from '../components/ExecutionConsole.vue'
import ExecutionDetailDrawer from '../components/ExecutionDetailDrawer.vue'
import type { ExecutionCaseResult, ExecutionView } from '../api/contracts'
import { useContextStore } from '../stores/context'
import { useExecutionsStore } from '../stores/executions'

const context = useContextStore()
const executions = useExecutionsStore()
const route = useRoute()
const router = useRouter()
const inspected = ref<ExecutionCaseResult | null>(null)

onMounted(async () => {
  await Promise.all([context.loadSavedContext(), context.loadOptions()])
  if (context.projectId) await executions.load(context.projectId)
  const executionId = typeof route.query.executionId === 'string' ? route.query.executionId : ''
  if (executionId) await executions.select(executionId)
})
onBeforeUnmount(() => executions.disconnect())

function edit(result: ExecutionCaseResult, execution: ExecutionView): void {
  void router.push({ name: 'workbench', query: {
    endpointId: result.endpoint_id, caseVersionId: result.case_version_id,
    projectId: execution.project_id, sourceRevisionId: execution.source_revision_id,
    environmentRevisionId: execution.environment_revision_id,
  } })
}
async function rerun(execution: ExecutionView): Promise<void> {
  await executions.rerunFailed(execution)
}
</script>

<template>
  <section class="workspace">
    <header class="page-toolbar">
      <div><p class="eyebrow">API TEST RUNS</p><h1>执行记录</h1><p class="page-subtitle">选择任务即可继续查看实时日志，不会重复发起请求。</p></div>
    </header>
    <p v-if="executions.error" class="inline-error">{{ executions.error }}</p>
    <ExecutionConsole
      :executions="executions.executions"
      :active="executions.active"
      :events="executions.events"
      :connection-state="executions.connectionState"
      :loading="executions.loading"
      @select="executions.select($event)"
      @cancel="executions.cancel($event)"
      @rerun="rerun"
      @reconnect="executions.reconnect($event)"
      @inspect="inspected = $event"
      @edit="edit"
    />
    <ExecutionDetailDrawer v-if="executions.active && inspected" :execution="executions.active" :initial-case-id="inspected.execution_case_id" @close="inspected = null" @edit="edit" @rerun="rerun" />
  </section>
</template>
