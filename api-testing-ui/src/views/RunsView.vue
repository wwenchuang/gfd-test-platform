<script setup lang="ts">
import { onBeforeUnmount, onMounted } from 'vue'
import { Play } from 'lucide-vue-next'
import { useRouter } from 'vue-router'

import ExecutionConsole from '../components/ExecutionConsole.vue'
import ExecutionDetailDrawer from '../components/ExecutionDetailDrawer.vue'
import type { ExecutionCaseResult, ExecutionView } from '../api/contracts'
import { useContextStore } from '../stores/context'
import { useExecutionsStore } from '../stores/executions'

const context = useContextStore()
const executions = useExecutionsStore()
const router = useRouter()

onMounted(async () => {
  await Promise.all([context.loadSavedContext(), context.loadOptions()])
  if (context.projectId) await executions.load(context.projectId)
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
async function runBaselines(): Promise<void> {
  if (!context.projectId || !context.sourceRevisionId || !context.environmentRevisionId) return
  await executions.runBaselines({
    projectId: context.projectId,
    sourceRevisionId: context.sourceRevisionId,
    environmentRevisionId: context.environmentRevisionId,
  })
}
</script>

<template>
  <section class="workspace">
    <header class="page-toolbar">
      <div><p class="eyebrow">API TEST RUNS</p><h1>执行记录</h1><p class="page-subtitle">选择任务即可继续查看实时日志，不会重复发起请求。</p></div>
      <button data-testid="run-baselines" class="primary-command" type="button" :disabled="executions.baselineStarting || !context.projectId || !context.sourceRevisionId || !context.environmentRevisionId" @click="runBaselines"><Play :size="15" />{{ executions.baselineStarting ? '正在创建' : '执行当前基线' }}</button>
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
    />
    <ExecutionDetailDrawer v-if="executions.active && ['DONE','CANCELLED'].includes(executions.active.state)" :execution="executions.active" @close="executions.active = null" @edit="edit" @rerun="rerun" />
  </section>
</template>
