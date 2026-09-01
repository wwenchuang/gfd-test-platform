<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import ExecutionConsole from '../components/ExecutionConsole.vue'
import ExecutionDetailDrawer from '../components/ExecutionDetailDrawer.vue'
import type { ExecutionCaseResult, ExecutionView } from '../api/contracts'
import { useContextStore } from '../stores/context'
import { useExecutionsStore } from '../stores/executions'
import { confirmApiExecution } from '../utils/executionConfirmation'
import { executionDisplayName } from '../utils/executionPresentation'

const context = useContextStore()
const executions = useExecutionsStore()
const route = useRoute()
const router = useRouter()
const inspected = ref<ExecutionCaseResult | null>(null)

onMounted(async () => {
  const initialExecutionId = requestedExecutionId()
  if (initialExecutionId) executions.prepareSelection(initialExecutionId)
  await Promise.all([context.loadSavedContext(), context.loadOptions()])
  if (context.projectId) await executions.load(context.projectId)
  const executionId = requestedExecutionId()
  if (
    executionId
    && executionId !== executions.active?.id
    && (executionId === initialExecutionId || executionId !== executions.selectingExecutionId)
  ) {
    await executions.select(executionId)
  } else if (!executionId) {
    await selectFirstEndpointExecution()
  }
})
watch(() => route.query.executionId, async value => {
  const executionId = typeof value === 'string' ? value : ''
  if (!executionId || executionId === executions.active?.id) return
  await executions.select(executionId)
})
onBeforeUnmount(() => executions.disconnect())

function requestedExecutionId(): string {
  return typeof route.query.executionId === 'string' ? route.query.executionId : ''
}

async function selectFirstEndpointExecution(): Promise<void> {
  const endpointId = typeof route.query.endpointId === 'string' ? route.query.endpointId : ''
  const endpointKey = typeof route.query.endpointKey === 'string' ? route.query.endpointKey : ''
  if (!endpointId && !endpointKey) return
  const matching = executions.executions.find(execution => execution.case_results.some(result => (
    result.endpoint_id === endpointId || Boolean(endpointKey && result.endpoint_stable_key === endpointKey)
  )))
  if (matching && matching.id !== executions.active?.id) await executions.select(matching.id)
}

function clearEndpointFilter(): void {
  const query = { ...route.query }
  delete query.endpointId
  delete query.endpointKey
  void router.replace({ query })
}

function edit(result: ExecutionCaseResult, execution: ExecutionView): void {
  void router.push({ name: 'workbench', query: {
    endpointId: result.endpoint_id, caseVersionId: result.case_version_id,
    projectId: execution.project_id, sourceRevisionId: execution.source_revision_id,
    environmentRevisionId: execution.environment_revision_id,
  } })
}
async function rerun(execution: ExecutionView): Promise<void> {
  if (!confirmApiExecution({
    action: '重新执行',
    environmentName: execution.environment_name || '原执行环境',
    targetName: executionDisplayName(execution),
    caseCount: execution.case_results.length || execution.summary.total,
  })) return
  const rerunExecution = await executions.rerunExecution(execution)
  if (!rerunExecution) return
  await router.push({
    name: 'runs',
    query: { ...route.query, executionId: rerunExecution.id },
  })
}

async function deleteExecution(executionId: string): Promise<void> {
  const wasActive = executions.active?.id === executionId
  await executions.deleteExecution(executionId)
  if (wasActive) inspected.value = null
}

async function deleteExecutions(executionIds: string[]): Promise<void> {
  await executions.deleteExecutions(executionIds)
  if (!executions.active || executionIds.includes(executions.active.id)) inspected.value = null
}

function inspectResult(result: ExecutionCaseResult): void {
  inspected.value = result
}

async function loadCaseEvidence(result: ExecutionCaseResult): Promise<void> {
  const executionId = executions.active?.id
  if (!executionId) return
  try {
    await executions.loadExecutionCase(executionId, result.execution_case_id)
  } catch {
    // The store keeps a visible, retryable error beside the selected case.
  }
}
</script>

<template>
  <section class="workspace">
    <header class="page-toolbar">
      <div><p class="eyebrow">执行记录</p><h1>执行记录</h1><p class="page-subtitle">选择任务即可继续查看实时日志，不会重复发起请求。</p></div>
    </header>
    <p v-if="executions.error" class="inline-error">{{ executions.error }}</p>
    <ExecutionConsole
      :executions="executions.executions"
      :active="executions.active"
      :events="executions.events"
      :connection-state="executions.connectionState"
      :loading="executions.loading || Boolean(executions.selectingExecutionId)"
      :loading-case-keys="executions.loadingCaseKeys"
      :case-evidence-errors="executions.caseEvidenceErrors"
      :endpoint-id="typeof route.query.endpointId === 'string' ? route.query.endpointId : ''"
      :endpoint-stable-key="typeof route.query.endpointKey === 'string' ? route.query.endpointKey : ''"
      @select="executions.select($event)"
      @cancel="executions.cancel($event)"
      @rerun="rerun"
      @reconnect="executions.reconnect($event)"
      @inspect="inspectResult"
      @load-evidence="loadCaseEvidence"
      @edit="edit"
      @delete="deleteExecution"
      @delete-many="deleteExecutions"
      @clear-endpoint-filter="clearEndpointFilter"
    />
    <ExecutionDetailDrawer v-if="executions.active && inspected" :execution="executions.active" :initial-case-id="inspected.execution_case_id" :loading-case-keys="executions.loadingCaseKeys" :case-evidence-errors="executions.caseEvidenceErrors" @load-evidence="loadCaseEvidence" @close="inspected = null" @edit="edit" @rerun="rerun" />
  </section>
</template>
