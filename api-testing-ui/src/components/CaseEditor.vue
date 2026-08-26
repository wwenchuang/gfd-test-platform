<script setup lang="ts">
import { computed, nextTick, ref, watch } from 'vue'
import { Code2, List, Play, Plus, Trash2 } from 'lucide-vue-next'

import type { ApiEndpoint, CaseDependencyOption, CaseDraft, InlineWorkflowStep, WorkflowVariableOption } from '../api/contracts'
import { validateCaseDraftLocally } from '../utils/caseDraftValidation'
import { workflowVariableOptions } from '../utils/workflowVariables'
import AssertionListEditor from './AssertionListEditor.vue'
import ExtractionListEditor from './ExtractionListEditor.vue'
import InlineWorkflowStepEditor from './InlineWorkflowStepEditor.vue'
import RequestConfigEditor from './RequestConfigEditor.vue'
import DependencyPicker from './DependencyPicker.vue'
import CaseValidationSummary from './CaseValidationSummary.vue'

type ProcessingPhase = 'pre' | 'post'

const props = withDefaults(defineProps<{ modelValue: CaseDraft; validationErrors?: Record<string, string>; validationWarnings?: Record<string, string>; saving?: boolean; debugging?: boolean; savedMessage?: string; dependencyOptions?: CaseDependencyOption[]; endpointOptions?: ApiEndpoint[]; environmentVariableNames?: string[]; environmentRevisionId?: string; environmentName?: string }>(), {
  validationErrors: () => ({}), validationWarnings: () => ({}), saving: false, debugging: false, savedMessage: '', dependencyOptions: () => [], endpointOptions: () => [], environmentVariableNames: () => [], environmentRevisionId: '', environmentName: '',
})
const emit = defineEmits<{ 'update:modelValue': [draft: CaseDraft]; save: []; debug: [] }>()
const mode = ref<'structured' | 'raw'>('structured')
const local = ref<CaseDraft>(normalizeDraft(props.modelValue))
const raw = ref(JSON.stringify(local.value, null, 2))
const rawError = ref('')
const advancedErrors = ref<Record<string, string>>({})
const requestEditorValid = ref(true)
const editorRoot = ref<HTMLElement | null>(null)
const setupEditor = ref<{ openStep(index: number): void } | null>(null)
const cleanupEditor = ref<{ openStep(index: number): void } | null>(null)
const dataRowsSection = ref<HTMLDetailsElement | null>(null)
const extractionsSection = ref<HTMLDetailsElement | null>(null)
const advancedSection = ref<HTMLDetailsElement | null>(null)
const localValidationErrors = computed(() => validateCaseDraftLocally(local.value))
const displayValidationErrors = computed(() => ({ ...props.validationErrors, ...localValidationErrors.value }))
const hasBlockingError = computed(() => Boolean(
  !requestEditorValid.value
  || rawError.value
  || Object.keys(advancedErrors.value).length
  || Object.keys(localValidationErrors.value).length,
))
const setupVariableOptions = computed(() => (local.value.processing.setup_steps || []).map((_, index) => variableOptions('setup', index)))
const cleanupVariableOptions = computed(() => (local.value.processing.cleanup_steps || []).map((_, index) => variableOptions('cleanup', index)))
const mainVariableOptions = computed(() => variableOptions('main', 0))
const dataRowsOpen = computed(() => Boolean(local.value.data_rows.length || hasFeedback('data_rows')))
const extractionsOpen = computed(() => Boolean(local.value.extractions.length || hasFeedback('extractions')))
const advancedOpen = computed(() => Boolean(
  local.value.dependencies.length
  || local.value.processing.pre.length
  || local.value.processing.post.length
  || hasFeedback('dependencies')
  || hasFeedback('processing.pre')
  || hasFeedback('processing.post'),
))
const previewInitialVariables = computed(() => ({
  ...((local.value.data_rows || []).find(row => row.enabled)?.values || {}),
}))

function validationMessages(prefix: string, source: Record<string, string>): Array<[string, string]> {
  return Object.entries(source).filter(([field]) => field === prefix || field.startsWith(`${prefix}.`) || field.startsWith(`${prefix}[`))
}

function hasFeedback(prefix: string): boolean {
  return [props.validationErrors, props.validationWarnings].some(source => (
    Object.keys(source).some(field => field === prefix || field.startsWith(`${prefix}.`) || field.startsWith(`${prefix}[`))
  ))
}

async function navigateToField(path: string): Promise<void> {
  const setup = path.match(/^processing\.setup_steps\[(\d+)]/)
  const cleanup = path.match(/^processing\.cleanup_steps\[(\d+)]/)
  if (setup) setupEditor.value?.openStep(Number(setup[1]))
  if (cleanup) cleanupEditor.value?.openStep(Number(cleanup[1]))
  if (path.startsWith('data_rows')) dataRowsSection.value!.open = true
  if (path.startsWith('extractions')) extractionsSection.value!.open = true
  if (path.startsWith('dependencies') || path.startsWith('processing.pre') || path.startsWith('processing.post')) advancedSection.value!.open = true
  await nextTick()
  const feedback = Array.from(editorRoot.value?.querySelectorAll<HTMLElement>('[data-error-for], [data-warning-for]') || [])
    .find(element => element.dataset.errorFor === path || element.dataset.warningFor === path)
  feedback?.scrollIntoView?.({ block: 'center', behavior: 'smooth' })
  feedback?.closest('label, .assertion-card, .extraction-row, .dependency-row')?.querySelector<HTMLElement>('input, select, textarea, button')?.focus()
}

async function editStep(target: { stage: 'setup' | 'main' | 'cleanup'; index: number }): Promise<void> {
  if (target.stage === 'setup') setupEditor.value?.openStep(target.index)
  if (target.stage === 'cleanup') cleanupEditor.value?.openStep(target.index)
  await nextTick()
  const selector = target.stage === 'main'
    ? '.workflow-main-heading'
    : `[data-testid="${target.stage}-step-body-${target.index}"]`
  editorRoot.value?.querySelector<HTMLElement>(selector)?.scrollIntoView?.({ block: 'start', behavior: 'smooth' })
}

defineExpose({ editStep })

watch(() => props.modelValue, value => {
  if (JSON.stringify(value) !== JSON.stringify(local.value)) {
    local.value = normalizeDraft(value)
    raw.value = JSON.stringify(value, null, 2)
    requestEditorValid.value = true
  }
}, { deep: true })

function publish(): void {
  raw.value = JSON.stringify(local.value, null, 2)
  emit('update:modelValue', clone(local.value))
}

function entries(value: Record<string, unknown>): Array<[string, unknown]> {
  return Object.entries(value)
}

function uniqueName(value: Record<string, unknown>, seed: string): string {
  let name = seed
  let index = 2
  while (Object.prototype.hasOwnProperty.call(value, name)) name = `${seed}${index++}`
  return name
}

function addDataRow(): void {
  local.value.data_rows.push({ name: `数据 ${local.value.data_rows.length + 1}`, values: {}, enabled: true })
  publish()
}

function addDataValue(index: number): void {
  const values = local.value.data_rows[index].values
  values[uniqueName(values, '变量')] = ''
  publish()
}

function renameDataValue(index: number, previous: string, next: string): void {
  const values = local.value.data_rows[index].values
  const name = next.trim()
  if (!name || (name !== previous && Object.prototype.hasOwnProperty.call(values, name))) return
  const rebuilt: Record<string, unknown> = {}
  for (const [key, value] of Object.entries(values)) rebuilt[key === previous ? name : key] = value
  local.value.data_rows[index].values = rebuilt
  publish()
}

function updateDataValue(index: number, name: string, value: string): void {
  local.value.data_rows[index].values[name] = parseScalar(value)
  publish()
}

function addDependency(): void {
  local.value.dependencies.push({ case_version_id: '', required: true, exports: [] })
  publish()
}

function selectedDependency(dependency: Record<string, unknown>): CaseDependencyOption | undefined {
  return props.dependencyOptions.find(option => option.id === dependency.case_version_id)
}

function disabledDependencyIds(rowIndex: number): string[] {
  return local.value.dependencies
    .filter((_, index) => index !== rowIndex)
    .map(dependency => String(dependency.case_version_id || ''))
    .filter(Boolean)
}

function selectDependency(dependency: Record<string, unknown>, versionId: string): void {
  dependency.case_version_id = versionId
  dependency.exports = [...(props.dependencyOptions.find(option => option.id === versionId)?.exports || [])]
  publish()
}

function toggleDependencyExport(dependency: Record<string, unknown>, name: string, enabled: boolean): void {
  const selected = Array.isArray(dependency.exports)
    ? dependency.exports.filter((item): item is string => typeof item === 'string')
    : []
  const exports = new Set<string>(selected)
  if (enabled) exports.add(name)
  else exports.delete(name)
  dependency.exports = [...exports]
  publish()
}

function addProcessing(phase: ProcessingPhase): void {
  local.value.processing[phase].push({ action: 'set_variable', name: '', value: '' })
  publish()
}

function updateWorkflowSteps(phase: 'setup_steps' | 'cleanup_steps', steps: InlineWorkflowStep[]): void {
  local.value.processing[phase] = steps
  publish()
}

function variableOptions(stage: 'setup' | 'main' | 'cleanup', index: number): WorkflowVariableOption[] {
  return workflowVariableOptions(local.value, stage, index, props.environmentVariableNames, props.dependencyOptions)
}

function updateRequest(request: CaseDraft['request']): void {
  local.value.request = request
  publish()
}

function updateAssertions(assertions: Array<Record<string, unknown>>): void {
  local.value.assertions = assertions
  publish()
}

function updateExtractions(extractions: Array<Record<string, unknown>>): void {
  local.value.extractions = extractions
  publish()
}

function changeProcessing(action: Record<string, unknown>): void {
  const type = String(action.action)
  for (const key of ['name', 'value', 'source', 'target']) delete action[key]
  if (type === 'set_variable') Object.assign(action, { name: '', value: '' })
  else if (type === 'remove_variable') Object.assign(action, { name: '' })
  else Object.assign(action, { source: '', target: '' })
  publish()
}

function applyRaw(): void {
  try {
    const parsed = JSON.parse(raw.value) as CaseDraft
    if (!parsed.request || typeof parsed.request !== 'object') throw new Error('missing request')
    local.value = normalizeDraft(parsed)
    requestEditorValid.value = true
    rawError.value = ''
    publish()
  } catch {
    rawError.value = '用例 JSON 格式不正确或缺少 request'
  }
}

function parseScalar(value: string): unknown {
  const text = value.trim()
  if (!text) return ''
  try { return JSON.parse(text) } catch { return value }
}

function renderValue(value: unknown): string {
  return typeof value === 'string' ? value : JSON.stringify(value)
}

function clone(value: CaseDraft): CaseDraft {
  return JSON.parse(JSON.stringify(value)) as CaseDraft
}

function normalizeDraft(value: CaseDraft): CaseDraft {
  const draft = clone(value)
  draft.processing = {
    pre: draft.processing?.pre || [],
    post: draft.processing?.post || [],
    setup_steps: draft.processing?.setup_steps || [],
    cleanup_steps: draft.processing?.cleanup_steps || [],
  }
  return draft
}
</script>

<template>
  <section ref="editorRoot" class="case-editor">
    <header class="editor-toolbar">
      <div><h2>测试用例</h2><span>{{ savedMessage || '修改后先保存，再调试' }}</span></div>
      <div class="segmented">
        <button data-testid="structured-tab" type="button" :class="{ active: mode === 'structured' }" @click="mode = 'structured'"><List :size="15" />结构化</button>
        <button data-testid="raw-tab" type="button" :class="{ active: mode === 'raw' }" @click="mode = 'raw'"><Code2 :size="15" />原始 JSON</button>
      </div>
    </header>

    <div v-if="mode === 'structured'" class="editor-form">
      <div class="form-grid"><label>用例名称<input v-model="local.name" data-testid="case-name" @input="publish" /></label><label>优先级<select v-model="local.priority" @change="publish"><option v-for="priority in ['P0','P1','P2','P3']" :key="priority">{{ priority }}</option></select></label></div>
      <label>测试目的<textarea v-model="local.purpose" rows="2" @input="publish" /></label>

      <CaseValidationSummary :setup-count="local.processing.setup_steps?.length || 0" :assertion-count="local.assertions.length" :cleanup-count="local.processing.cleanup_steps?.length || 0" :errors="displayValidationErrors" :warnings="validationWarnings" @navigate="navigateToField" />

      <InlineWorkflowStepEditor ref="setupEditor" :model-value="local.processing.setup_steps || []" stage="setup" :endpoint-options="endpointOptions" :validation-errors="validationErrors" :variable-options="setupVariableOptions" :environment-revision-id="environmentRevisionId" :environment-name="environmentName" :initial-variables="previewInitialVariables" :processing-pre="local.processing.pre" @update:model-value="updateWorkflowSteps('setup_steps', $event)" />

      <div class="workflow-main-heading"><span>主体请求</span><small>验证目标接口的真实业务结果，并将响应变量传给清理步骤。</small></div>

      <RequestConfigEditor :model-value="local.request" :errors="displayValidationErrors" :warnings="validationWarnings" :variable-options="mainVariableOptions" @update:model-value="updateRequest" @validity="requestEditorValid = $event" />

      <details ref="dataRowsSection" data-testid="data-rows-section" class="optional-editor-section" :open="dataRowsOpen"><summary><span>测试数据</span><small>{{ local.data_rows.length ? `${local.data_rows.length} 组` : '可选' }}</small></summary><section class="editor-section"><div class="section-heading"><strong>数据行</strong><button class="mini-icon" type="button" title="添加数据行" @click="addDataRow"><Plus :size="15" /></button></div>
        <p v-if="!local.data_rows.length" class="compact-empty">当前用例只执行一次，不使用多组数据。</p>
        <article v-for="(row, rowIndex) in local.data_rows" :key="rowIndex" class="data-card"><div class="data-card-head"><input v-model="row.name" aria-label="数据名称" @input="publish" /><label class="toggle-line"><input v-model="row.enabled" type="checkbox" @change="publish" />启用</label><button class="mini-icon danger" type="button" title="删除数据" @click="local.data_rows.splice(rowIndex, 1); publish()"><Trash2 :size="14" /></button></div>
          <div v-for="([name, value], valueIndex) in entries(row.values)" :key="`${name}-${valueIndex}`" class="key-value-row"><input :value="name" aria-label="变量名" @change="renameDataValue(rowIndex, name, ($event.target as HTMLInputElement).value)" /><input :value="renderValue(value)" aria-label="变量值" @input="updateDataValue(rowIndex, name, ($event.target as HTMLInputElement).value)" /><button class="mini-icon danger" type="button" title="删除变量" @click="delete row.values[name]; publish()"><Trash2 :size="14" /></button></div>
          <button class="row-add" type="button" @click="addDataValue(rowIndex)"><Plus :size="14" />添加变量</button>
          <small v-for="([field, message]) in validationMessages(`data_rows[${rowIndex}]`, validationErrors)" :key="field" :data-error-for="field" class="field-error">{{ message }}</small>
          <small v-for="([field, message]) in validationMessages(`data_rows[${rowIndex}]`, validationWarnings)" :key="field" :data-warning-for="field" class="field-warning">{{ message }}</small>
        </article>
      </section></details>

      <AssertionListEditor :model-value="local.assertions" :errors="displayValidationErrors" :warnings="validationWarnings" @update:model-value="updateAssertions" />

      <details ref="extractionsSection" data-testid="extractions-section" class="optional-editor-section" :open="extractionsOpen"><summary><span>主体输出变量</span><small>{{ local.extractions.length ? `${local.extractions.length} 个` : '可选' }}</small></summary><ExtractionListEditor :model-value="local.extractions" :errors="validationErrors" :warnings="validationWarnings" @update:model-value="updateExtractions" /></details>

      <InlineWorkflowStepEditor ref="cleanupEditor" :model-value="local.processing.cleanup_steps || []" stage="cleanup" :endpoint-options="endpointOptions" :validation-errors="validationErrors" :variable-options="cleanupVariableOptions" @update:model-value="updateWorkflowSteps('cleanup_steps', $event)" />

      <details ref="advancedSection" data-testid="advanced-section" class="advanced-editor" :open="advancedOpen"><summary>共享用例依赖与变量处理</summary>
        <section class="editor-section">
          <div class="section-heading"><strong>用例依赖</strong><button data-testid="add-dependency" class="mini-icon" type="button" title="添加依赖" @click="addDependency"><Plus :size="14" /></button></div>
          <p v-if="!local.dependencies.length" class="compact-empty">添加前置用例后，可将其响应变量用于当前请求。</p>
          <div v-for="(dependency, index) in local.dependencies" :key="index" class="dependency-row">
            <div class="dependency-case-picker"><span>前置用例</span><DependencyPicker :model-value="String(dependency.case_version_id || '')" :options="dependencyOptions" :disabled-ids="disabledDependencyIds(index)" @update:model-value="selectDependency(dependency, $event)" /><small v-if="dependency.case_version_id && !selectedDependency(dependency)" class="field-warning">历史依赖当前不在可选列表中，保存时仍会保留。</small></div>
            <div class="dependency-exports">
              <span>传递变量</span>
              <div v-if="selectedDependency(dependency)?.exports.length" class="dependency-export-options">
                <label v-for="name in selectedDependency(dependency)?.exports" :key="name" class="toggle-line"><input :data-testid="`dependency-export-${index}-${name}`" type="checkbox" :checked="(dependency.exports as string[] || []).includes(name)" @change="toggleDependencyExport(dependency, name, ($event.target as HTMLInputElement).checked)" />{{ name }}</label>
              </div>
              <small v-else>该用例还没有配置响应提取变量</small>
            </div>
            <label class="toggle-line"><input v-model="dependency.required" type="checkbox" @change="publish" />必需</label>
            <button class="mini-icon danger" type="button" title="删除依赖" @click="local.dependencies.splice(index, 1); publish()"><Trash2 :size="14" /></button>
            <small v-for="([field, message]) in validationMessages(`dependencies[${index}]`, validationErrors)" :key="field" :data-error-for="field" class="field-error row-feedback">{{ message }}</small>
            <small v-for="([field, message]) in validationMessages(`dependencies[${index}]`, validationWarnings)" :key="field" :data-warning-for="field" class="field-warning row-feedback">{{ message }}</small>
          </div>
        </section>
        <section v-for="phase in (['pre','post'] as ProcessingPhase[])" :key="phase" class="editor-section"><div class="section-heading"><strong>{{ phase === 'pre' ? '前置处理' : '后置处理' }}</strong><button class="mini-icon" type="button" title="添加处理" @click="addProcessing(phase)"><Plus :size="14" /></button></div><div v-for="(action, index) in local.processing[phase]" :key="index" class="processing-row"><select v-model="action.action" aria-label="处理动作" @change="changeProcessing(action)"><option value="set_variable">设置变量</option><option value="copy_variable">复制变量</option><option value="remove_variable">删除变量</option><option value="json_encode">JSON 编码</option><option value="json_decode">JSON 解码</option></select><input v-if="'name' in action" v-model="action.name" placeholder="变量名" @input="publish" /><input v-if="'source' in action" v-model="action.source" placeholder="来源变量" @input="publish" /><input v-if="'target' in action" v-model="action.target" placeholder="目标变量" @input="publish" /><input v-if="'value' in action" :value="renderValue(action.value)" placeholder="值" @input="action.value = parseScalar(($event.target as HTMLInputElement).value); publish()" /><button class="mini-icon danger" type="button" title="删除处理" @click="local.processing[phase].splice(index, 1); publish()"><Trash2 :size="14" /></button><small v-for="([field, message]) in validationMessages(`processing.${phase}[${index}]`, validationErrors)" :key="field" :data-error-for="field" class="field-error row-feedback">{{ message }}</small><small v-for="([field, message]) in validationMessages(`processing.${phase}[${index}]`, validationWarnings)" :key="field" :data-warning-for="field" class="field-warning row-feedback">{{ message }}</small></div></section>
      </details>
    </div>

    <div v-else class="raw-editor"><textarea v-model="raw" rows="26" spellcheck="false" @blur="applyRaw" /><p v-if="rawError" class="field-error">{{ rawError }}</p></div>
    <footer class="editor-footer"><span role="status">{{ savedMessage || (hasBlockingError ? '请先处理阻塞错误' : '可保存草稿或直接调试') }}</span><small v-if="Object.keys(displayValidationErrors).length">错误 {{ Object.keys(displayValidationErrors).length }}</small><small v-if="Object.keys(validationWarnings).length">警告 {{ Object.keys(validationWarnings).length }}</small><button data-testid="save-case-draft" class="secondary-command" type="button" :disabled="saving || hasBlockingError" @click="emit('save')">{{ saving ? '保存中...' : '保存草稿' }}</button><button data-testid="save-and-debug" class="primary-command" type="button" :disabled="saving || debugging || hasBlockingError" @click="emit('debug')"><Play :size="15" />{{ debugging ? '调试中...' : '保存并调试' }}</button></footer>
  </section>
</template>
