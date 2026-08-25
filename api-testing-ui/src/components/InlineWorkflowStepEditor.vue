<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { Plus } from 'lucide-vue-next'

import type { ApiEndpoint, InlineWorkflowStep, WorkflowVariableOption } from '../api/contracts'
import { groupEndpoints } from '../utils/endpointGroups'
import { withLegacyVariables } from '../utils/workflowVariables'
import EndpointPicker from './EndpointPicker.vue'
import AssertionListEditor from './AssertionListEditor.vue'
import ExtractionListEditor from './ExtractionListEditor.vue'
import RequestConfigEditor from './RequestConfigEditor.vue'
import WorkflowStepCard from './WorkflowStepCard.vue'
import VariablePicker from './VariablePicker.vue'

const props = defineProps<{
  modelValue: InlineWorkflowStep[]
  stage: 'setup' | 'cleanup'
  endpointOptions?: ApiEndpoint[]
  validationErrors?: Record<string, string>
  variableOptions?: WorkflowVariableOption[][]
}>()
const emit = defineEmits<{ 'update:modelValue': [steps: InlineWorkflowStep[]] }>()
const jsonErrors = ref<Record<string, string>>({})
const pickerOpen = ref(false)
const activeIndex = ref<number | null>(props.modelValue.length ? 0 : null)
const groups = computed(() => groupEndpoints(props.endpointOptions || [])
  .map(([name, endpoints]) => ({ name, endpoints })))
const stageLabel = computed(() => props.stage === 'setup' ? '前置步骤' : '清理步骤')
const stageHint = computed(() => props.stage === 'setup'
  ? '按顺序获取主体请求需要的真实业务数据。'
  : '主体结束后始终执行，只回收本次运行产生或修改的数据。')

function clone<T>(value: T): T {
  return JSON.parse(JSON.stringify(value)) as T
}

function update(mutator: (steps: InlineWorkflowStep[]) => void): void {
  const steps = clone(props.modelValue || [])
  mutator(steps)
  emit('update:modelValue', steps)
}

function addStep(): void {
  pickerOpen.value = true
}

function addManualStep(): void {
  update(steps => steps.push({
    name: `${stageLabel.value} ${steps.length + 1}`,
    enabled: true,
    request: {
      method: 'GET', path: '/', service: 'default',
      path_params: {}, query: {}, headers: {}, cookies: {}, body: null,
    },
    assertions: [
      { type: 'status_code', operator: 'equals', expected: 200, timeout_ms: 0, enabled: true },
      { type: 'json_path', operator: 'equals', path: '$.code', expected: 0, timeout_ms: 0, enabled: true },
    ],
    extractions: [],
    required_variables: [],
  }))
  activeIndex.value = props.modelValue.length
  pickerOpen.value = false
}

function addEndpointStep(endpoint: ApiEndpoint): void {
  update(steps => steps.push({
    name: endpoint.summary || `${endpoint.method} ${endpoint.path}`,
    enabled: true,
    request: {
      method: endpoint.method, path: endpoint.path, service: 'default',
      path_params: {}, query: {}, headers: {}, cookies: {}, body: null,
    },
    assertions: [
      { type: 'status_code', operator: 'equals', expected: 200, timeout_ms: 0, enabled: true },
      { type: 'json_path', operator: 'equals', path: '$.code', expected: 0, timeout_ms: 0, enabled: true },
    ],
    extractions: [],
    required_variables: [],
  }))
  activeIndex.value = props.modelValue.length
  pickerOpen.value = false
}

function patchStep(index: number, patch: Partial<InlineWorkflowStep>): void {
  update(steps => { steps[index] = { ...steps[index], ...patch } })
}

function patchRequest(index: number, patch: Partial<InlineWorkflowStep['request']>): void {
  update(steps => {
    steps[index].request = { ...steps[index].request, ...patch }
    if (!['GET', 'HEAD'].includes(steps[index].request.method)) delete steps[index].polling
  })
}

function updatePolling(index: number, enabled: boolean): void {
  patchStep(index, {
    polling: enabled ? { max_attempts: 10, interval_ms: 2000 } : undefined,
  })
}

function patchPolling(index: number, patch: Partial<NonNullable<InlineWorkflowStep['polling']>>): void {
  const current = props.modelValue[index].polling || { max_attempts: 10, interval_ms: 2000 }
  patchStep(index, { polling: { ...current, ...patch } })
}

function selectEndpoint(index: number, endpointId: string): void {
  const endpoint = (props.endpointOptions || []).find(item => item.id === endpointId)
  if (!endpoint) return
  update(steps => {
    const step = steps[index]
    step.name = endpoint.summary || `${endpoint.method} ${endpoint.path}`
    step.request = {
      ...step.request,
      method: endpoint.method,
      path: endpoint.path,
      service: 'default',
      path_params: {},
      query: {},
      headers: {},
      cookies: {},
      body: null,
    }
    if (!['GET', 'HEAD'].includes(endpoint.method)) delete step.polling
  })
}

function matchedEndpointId(step: InlineWorkflowStep): string {
  return (props.endpointOptions || []).find(item => (
    item.method === step.request.method && item.path === step.request.path
  ))?.id || ''
}

function move(index: number, offset: number): void {
  const target = index + offset
  if (target < 0 || target >= props.modelValue.length) return
  update(steps => {
    const [step] = steps.splice(index, 1)
    steps.splice(target, 0, step)
  })
  if (activeIndex.value === index) activeIndex.value = target
  else if (activeIndex.value === target) activeIndex.value = index
}

function duplicate(index: number): void {
  update(steps => {
    const copy = clone(steps[index])
    copy.name = `${copy.name} 副本`
    steps.splice(index + 1, 0, copy)
  })
  activeIndex.value = index + 1
}

function remove(index: number): void {
  const step = props.modelValue[index]
  if (!globalThis.confirm(`确认删除步骤“${step.name}”？`)) return
  update(steps => steps.splice(index, 1))
  if (props.modelValue.length <= 1) activeIndex.value = null
  else activeIndex.value = Math.min(index, props.modelValue.length - 2)
}

function toggleActive(index: number): void {
  activeIndex.value = activeIndex.value === index ? null : index
}

function availableVariables(index: number): WorkflowVariableOption[] {
  return withLegacyVariables(props.variableOptions?.[index] || [], props.modelValue[index].required_variables || [])
}

function requestConfig(step: InlineWorkflowStep): string {
  return JSON.stringify({
    path_params: step.request.path_params,
    query: step.request.query,
    headers: step.request.headers,
    cookies: step.request.cookies,
    body: step.request.body,
  }, null, 2)
}

function updateJson(index: number, field: 'request' | 'assertions' | 'extractions', raw: string): void {
  const key = `${field}-${index}`
  try {
    const parsed = JSON.parse(raw) as unknown
    if (field === 'request') {
      if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) throw new Error('object')
      const value = parsed as Record<string, unknown>
      patchRequest(index, {
        path_params: recordValue(value.path_params),
        query: recordValue(value.query),
        headers: recordValue(value.headers),
        cookies: recordValue(value.cookies),
        body: value.body ?? null,
      })
    } else {
      if (!Array.isArray(parsed)) throw new Error('array')
      patchStep(index, { [field]: parsed as Array<Record<string, unknown>> })
    }
    delete jsonErrors.value[key]
  } catch {
    jsonErrors.value[key] = field === 'request' ? '请求配置必须是 JSON 对象' : '配置必须是 JSON 数组'
  }
}

function recordValue(value: unknown): Record<string, unknown> {
  return value && typeof value === 'object' && !Array.isArray(value)
    ? value as Record<string, unknown>
    : {}
}

function validationMessages(index: number): string[] {
  const prefix = stepPrefix(index)
  return Object.entries(props.validationErrors || {})
    .filter(([field]) => field === prefix || field.startsWith(`${prefix}.`))
    .map(([, message]) => message)
}

function stepPrefix(index: number): string {
  return `processing.${props.stage === 'setup' ? 'setup_steps' : 'cleanup_steps'}[${index}]`
}

watch(() => props.modelValue.length, length => {
  if (!length) activeIndex.value = null
  else if (activeIndex.value !== null && activeIndex.value >= length) activeIndex.value = length - 1
})

watch(() => props.validationErrors, errors => {
  const field = Object.keys(errors || {}).find(path => path.startsWith(`processing.${props.stage === 'setup' ? 'setup_steps' : 'cleanup_steps'}[`))
  const match = field?.match(/\[(\d+)\]/)
  if (match) activeIndex.value = Number(match[1])
}, { deep: true, immediate: true })
</script>

<template>
  <section class="workflow-stage" :class="`workflow-stage-${stage}`">
    <EndpointPicker
      :open="pickerOpen"
      :endpoints="endpointOptions || []"
      :title="`添加${stageLabel}`"
      @select="addEndpointStep"
      @manual="addManualStep"
      @close="pickerOpen = false"
    />
    <header class="workflow-stage-heading">
      <div><strong>{{ stageLabel }}</strong><span>{{ stageHint }}</span></div>
      <button :data-testid="`add-${stage}-step`" class="secondary-command" type="button" @click="addStep"><Plus :size="14" />添加步骤</button>
    </header>
    <p v-if="!modelValue.length" class="compact-empty">当前没有{{ stageLabel }}。</p>
    <WorkflowStepCard
      v-for="(step, index) in modelValue"
      :key="`${stage}-${index}`"
      :step="step"
      :index="index"
      :stage="stage"
      :active="activeIndex === index"
      :issue-count="validationMessages(index).length"
      :first="index === 0"
      :last="index === modelValue.length - 1"
      @toggle="toggleActive(index)"
      @enabled="patchStep(index, { enabled: $event })"
      @move="move(index, $event)"
      @duplicate="duplicate(index)"
      @remove="remove(index)"
    >
        <div class="workflow-step-identity">
          <label>步骤名称<input :value="step.name" @input="patchStep(index, { name: ($event.target as HTMLInputElement).value })" /></label>
          <label>选择接口
            <select :data-testid="`${stage}-endpoint-${index}`" :value="matchedEndpointId(step)" @change="selectEndpoint(index, ($event.target as HTMLSelectElement).value)">
              <option value="">手工配置请求</option>
              <optgroup v-for="group in groups" :key="group.name" :label="group.name">
                <option v-for="endpoint in group.endpoints" :key="endpoint.id" :value="endpoint.id">{{ endpoint.summary || endpoint.path }} · {{ endpoint.method }} {{ endpoint.path }}</option>
              </optgroup>
            </select>
          </label>
          <div class="workflow-required-variables"><span>必需变量</span><VariablePicker :model-value="step.required_variables" :options="availableVariables(index)" :test-id-prefix="`${stage}-${index}`" @update:model-value="patchStep(index, { required_variables: $event })" /></div>
        </div>
        <RequestConfigEditor :model-value="step.request" :errors="validationErrors" :prefix="`${stepPrefix(index)}.request`" :test-id-prefix="`${stage}-${index}`" :variable-options="availableVariables(index)" @update:model-value="patchStep(index, { request: $event })" />
        <AssertionListEditor :model-value="step.assertions" :errors="validationErrors" :prefix="`${stepPrefix(index)}.assertions`" :test-id-prefix="`${stage}-${index}`" @update:model-value="patchStep(index, { assertions: $event })" />
        <ExtractionListEditor :model-value="step.extractions" :errors="validationErrors" :prefix="`${stepPrefix(index)}.extractions`" :test-id-prefix="`${stage}-${index}`" @update:model-value="patchStep(index, { extractions: $event })" />
        <details :data-testid="`${stage}-${index}-raw-config`" class="workflow-advanced">
          <summary>高级配置</summary>
          <div class="workflow-polling">
            <label class="toggle-line">
              <input
                :data-testid="`${stage}-polling-${index}`"
                type="checkbox"
                :checked="Boolean(step.polling)"
                :disabled="!['GET', 'HEAD'].includes(step.request.method)"
                @change="updatePolling(index, ($event.target as HTMLInputElement).checked)"
              />轮询直到断言通过
            </label>
            <template v-if="step.polling">
              <label>最多尝试<input :data-testid="`${stage}-poll-attempts-${index}`" type="number" min="2" max="30" :value="step.polling.max_attempts" @input="patchPolling(index, { max_attempts: Number(($event.target as HTMLInputElement).value) })" /></label>
              <label>间隔毫秒<input :data-testid="`${stage}-poll-interval-${index}`" type="number" min="100" max="30000" step="100" :value="step.polling.interval_ms" @input="patchPolling(index, { interval_ms: Number(($event.target as HTMLInputElement).value) })" /></label>
            </template>
            <small v-else-if="!['GET', 'HEAD'].includes(step.request.method)">轮询仅支持 GET、HEAD 查询，避免重复创建或修改数据。</small>
          </div>
          <details class="workflow-raw-config"><summary>原始 JSON</summary><div class="workflow-json-grid">
            <label>参数与请求体<textarea :value="requestConfig(step)" rows="8" @change="updateJson(index, 'request', ($event.target as HTMLTextAreaElement).value)" /><small v-if="jsonErrors[`request-${index}`]" class="field-error">{{ jsonErrors[`request-${index}`] }}</small></label>
            <label>业务断言<textarea :value="JSON.stringify(step.assertions, null, 2)" rows="8" @change="updateJson(index, 'assertions', ($event.target as HTMLTextAreaElement).value)" /><small v-if="jsonErrors[`assertions-${index}`]" class="field-error">{{ jsonErrors[`assertions-${index}`] }}</small></label>
            <label>响应提取<textarea :value="JSON.stringify(step.extractions, null, 2)" rows="8" @change="updateJson(index, 'extractions', ($event.target as HTMLTextAreaElement).value)" /><small v-if="jsonErrors[`extractions-${index}`]" class="field-error">{{ jsonErrors[`extractions-${index}`] }}</small></label>
          </div></details>
        </details>
        <small v-for="message in validationMessages(index)" :key="message" class="field-error">{{ message }}</small>
    </WorkflowStepCard>
  </section>
</template>
