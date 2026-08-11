<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { Code2, List, Plus, Trash2 } from 'lucide-vue-next'

import type { CaseDraft } from '../api/contracts'

type RequestMapField = 'headers' | 'query' | 'path_params' | 'cookies'
type ProcessingPhase = 'pre' | 'post'

const props = withDefaults(defineProps<{ modelValue: CaseDraft; validationErrors?: Record<string, string>; validationWarnings?: Record<string, string>; saving?: boolean; savedMessage?: string }>(), {
  validationErrors: () => ({}), validationWarnings: () => ({}), saving: false, savedMessage: '',
})
const emit = defineEmits<{ 'update:modelValue': [draft: CaseDraft]; save: [] }>()
const mode = ref<'structured' | 'raw'>('structured')
const local = ref<CaseDraft>(clone(props.modelValue))
const raw = ref(JSON.stringify(local.value, null, 2))
const rawError = ref('')
const bodyText = ref(JSON.stringify(local.value.request.body, null, 2))
const bodyError = ref('')
const advancedErrors = ref<Record<string, string>>({})
const pendingRequestEntries: Record<RequestMapField, Set<string>> = {
  headers: new Set(), query: new Set(), path_params: new Set(), cookies: new Set(),
}

const requestSections = [
  { label: '请求头', field: 'headers' as const },
  { label: '查询参数', field: 'query' as const },
  { label: '路径参数', field: 'path_params' as const },
  { label: 'Cookie', field: 'cookies' as const },
]
const assertionOperators: Record<string, string[]> = {
  status_code: ['equals', 'not_equals', 'in'],
  json_path: ['equals', 'not_equals', 'contains', 'not_contains', 'exists', 'not_exists', 'greater_than', 'less_than', 'matches', 'in'],
  header: ['equals', 'not_equals', 'contains', 'not_contains', 'exists', 'not_exists', 'matches', 'in'],
  response_time: ['greater_than', 'less_than'],
  schema: ['equals'],
}
const hasBlockingError = computed(() => Boolean(bodyError.value || rawError.value || Object.keys(advancedErrors.value).length))

function validationMessages(prefix: string, source: Record<string, string>): Array<[string, string]> {
  return Object.entries(source).filter(([field]) => field === prefix || field.startsWith(`${prefix}.`) || field.startsWith(`${prefix}[`))
}

function missingRequestMessages(field: RequestMapField): Array<[string, string]> {
  const prefix = `request.${field}.`
  return Object.entries(props.validationErrors).filter(([path]) => (
    path.startsWith(prefix)
    && !Object.prototype.hasOwnProperty.call(local.value.request[field], path.slice(prefix.length))
  ))
}

watch(() => props.modelValue, value => {
  if (JSON.stringify(value) !== JSON.stringify(local.value)) {
    local.value = clone(value)
    raw.value = JSON.stringify(value, null, 2)
    bodyText.value = JSON.stringify(value.request.body, null, 2)
    bodyError.value = ''
    for (const pending of Object.values(pendingRequestEntries)) pending.clear()
  }
}, { deep: true })

function publish(): void {
  raw.value = JSON.stringify(local.value, null, 2)
  const published = clone(local.value)
  for (const field of Object.keys(pendingRequestEntries) as RequestMapField[]) {
    for (const name of pendingRequestEntries[field]) delete published.request[field][name]
  }
  emit('update:modelValue', published)
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

function addRequestEntry(field: RequestMapField): void {
  const target = local.value.request[field]
  const name = uniqueName(target, '新参数')
  target[name] = ''
  pendingRequestEntries[field].add(name)
}

function renameRequestEntry(field: RequestMapField, previous: string, next: string): void {
  const name = next.trim()
  const target = local.value.request[field]
  if (!name || (name !== previous && Object.prototype.hasOwnProperty.call(target, name))) return
  pendingRequestEntries[field].delete(previous)
  const rebuilt: Record<string, unknown> = {}
  for (const [key, value] of Object.entries(target)) rebuilt[key === previous ? name : key] = value
  local.value.request[field] = rebuilt
  publish()
}

function updateRequestValue(field: RequestMapField, name: string, value: string): void {
  local.value.request[field][name] = parseScalar(value)
  publish()
}

function updateRequestValueAt(field: RequestMapField, index: number, value: string): void {
  const name = Object.keys(local.value.request[field])[index]
  if (name) updateRequestValue(field, name, value)
}

function removeRequestEntry(field: RequestMapField, name: string): void {
  pendingRequestEntries[field].delete(name)
  delete local.value.request[field][name]
  publish()
}

function updateBody(value: string): void {
  bodyText.value = value
  try {
    local.value.request.body = JSON.parse(value)
    bodyError.value = ''
    publish()
  } catch {
    bodyError.value = '请求体 JSON 格式不正确'
  }
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

function addAssertion(): void {
  local.value.assertions.push({ type: 'status_code', operator: 'equals', expected: 200, timeout_ms: 0, enabled: true })
  publish()
}

function changeAssertionType(assertion: Record<string, unknown>): void {
  const operators = assertionOperators[String(assertion.type)] || ['equals']
  if (!operators.includes(String(assertion.operator))) assertion.operator = operators[0]
  if (assertion.type === 'response_time' && typeof assertion.expected !== 'number') assertion.expected = 1000
  if (assertion.type === 'status_code' && typeof assertion.expected !== 'number') assertion.expected = 200
  publish()
}

function updateExpected(assertion: Record<string, unknown>, value: string): void {
  assertion.expected = ['status_code', 'response_time'].includes(String(assertion.type)) ? numericOrText(value) : parseScalar(value)
  publish()
}

function addExtraction(): void {
  local.value.extractions.push({ target: `变量${local.value.extractions.length + 1}`, type: 'json_path', path: '$.data', required: true })
  publish()
}

function addDependency(): void {
  local.value.dependencies.push({ case_version_id: '', required: true, exports: [] })
  publish()
}

function updateExports(dependency: Record<string, unknown>, value: string): void {
  dependency.exports = value.split(',').map(item => item.trim()).filter(Boolean)
  publish()
}

function addProcessing(phase: ProcessingPhase): void {
  local.value.processing[phase].push({ action: 'set_variable', name: '', value: '' })
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
    local.value = parsed
    bodyText.value = JSON.stringify(parsed.request.body, null, 2)
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

function numericOrText(value: string): number | string {
  const numeric = Number(value)
  return value.trim() !== '' && Number.isFinite(numeric) ? numeric : value
}

function renderValue(value: unknown): string {
  return typeof value === 'string' ? value : JSON.stringify(value)
}

function clone(value: CaseDraft): CaseDraft {
  return JSON.parse(JSON.stringify(value)) as CaseDraft
}
</script>

<template>
  <section class="case-editor">
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

      <fieldset><legend>请求</legend><div class="form-grid request-line"><label>方法<select v-model="local.request.method" @change="publish"><option v-for="method in ['GET','POST','PUT','PATCH','DELETE','HEAD','OPTIONS']" :key="method">{{ method }}</option></select></label><label class="grow">路径<input v-model="local.request.path" @input="publish" /><small v-if="validationErrors['request.path']" data-error-for="request.path" class="field-error">{{ validationErrors['request.path'] }}</small></label><label>服务<input v-model="local.request.service" @input="publish" /></label></div></fieldset>

      <div class="structured-grid">
        <fieldset v-for="section in requestSections" :key="section.field" class="row-editor"><legend>{{ section.label }}</legend>
          <div v-for="([name, value], index) in entries(local.request[section.field])" :key="`${section.field}-${index}`" class="key-value-row">
            <input :data-testid="`${section.field}-name`" :value="name" aria-label="参数名" @change="renameRequestEntry(section.field, name, ($event.target as HTMLInputElement).value)" />
            <input :data-testid="`${section.field}-value`" :value="renderValue(value)" aria-label="参数值" @input="updateRequestValueAt(section.field, index, ($event.target as HTMLInputElement).value)" />
            <button class="mini-icon danger" type="button" title="删除参数" @click="removeRequestEntry(section.field, name)"><Trash2 :size="14" /></button>
            <small v-for="([field, message]) in validationMessages(`request.${section.field}.${name}`, validationErrors)" :key="field" :data-error-for="field" class="field-error row-feedback">{{ message }}</small>
            <small v-for="([field, message]) in validationMessages(`request.${section.field}.${name}`, validationWarnings)" :key="field" :data-warning-for="field" class="field-warning row-feedback">{{ message }}</small>
          </div>
          <small v-for="([field, message]) in validationMessages(`request.${section.field}`, validationErrors).filter(([field]) => !field.split('.').slice(2).length)" :key="field" :data-error-for="field" class="field-error">{{ message }}</small>
          <small v-for="([field, message]) in missingRequestMessages(section.field)" :key="field" :data-error-for="field" class="field-error">{{ message }}</small>
          <button :data-testid="`${section.field}-add`" class="row-add" type="button" @click="addRequestEntry(section.field)"><Plus :size="14" />添加参数</button>
        </fieldset>
      </div>

      <label>请求体（JSON）<textarea data-testid="request-body" :value="bodyText" rows="6" @input="updateBody(($event.target as HTMLTextAreaElement).value)" /><small v-if="bodyError" data-error-for="request.body" class="field-error">{{ bodyError }}</small><template v-else><small v-for="([field, message]) in validationMessages('request.body', validationErrors)" :key="field" :data-error-for="field" class="field-error">{{ message }}</small></template></label>

      <section class="editor-section"><div class="section-heading"><strong>测试数据</strong><button class="mini-icon" type="button" title="添加数据行" @click="addDataRow"><Plus :size="15" /></button></div>
        <p v-if="!local.data_rows.length" class="compact-empty">当前用例只执行一次，不使用多组数据。</p>
        <article v-for="(row, rowIndex) in local.data_rows" :key="rowIndex" class="data-card"><div class="data-card-head"><input v-model="row.name" aria-label="数据名称" @input="publish" /><label class="toggle-line"><input v-model="row.enabled" type="checkbox" @change="publish" />启用</label><button class="mini-icon danger" type="button" title="删除数据" @click="local.data_rows.splice(rowIndex, 1); publish()"><Trash2 :size="14" /></button></div>
          <div v-for="([name, value], valueIndex) in entries(row.values)" :key="`${name}-${valueIndex}`" class="key-value-row"><input :value="name" aria-label="变量名" @change="renameDataValue(rowIndex, name, ($event.target as HTMLInputElement).value)" /><input :value="renderValue(value)" aria-label="变量值" @input="updateDataValue(rowIndex, name, ($event.target as HTMLInputElement).value)" /><button class="mini-icon danger" type="button" title="删除变量" @click="delete row.values[name]; publish()"><Trash2 :size="14" /></button></div>
          <button class="row-add" type="button" @click="addDataValue(rowIndex)"><Plus :size="14" />添加变量</button>
          <small v-for="([field, message]) in validationMessages(`data_rows[${rowIndex}]`, validationErrors)" :key="field" :data-error-for="field" class="field-error">{{ message }}</small>
          <small v-for="([field, message]) in validationMessages(`data_rows[${rowIndex}]`, validationWarnings)" :key="field" :data-warning-for="field" class="field-warning">{{ message }}</small>
        </article>
      </section>

      <section class="editor-section"><div class="section-heading"><strong>断言</strong><button class="mini-icon" type="button" title="增加断言" @click="addAssertion"><Plus :size="15" /></button></div>
        <div v-for="(assertion, index) in local.assertions" :key="index" class="assertion-card">
          <label>类型<select v-model="assertion.type" @change="changeAssertionType(assertion)"><option value="status_code">状态码</option><option value="json_path">JSON Path</option><option value="header">响应头</option><option value="response_time">响应时间</option><option value="schema">Schema</option></select></label>
          <label>比较<select v-model="assertion.operator" @change="publish"><option v-for="operator in assertionOperators[String(assertion.type)] || ['equals']" :key="operator" :value="operator">{{ operator }}</option></select></label>
          <label v-if="['json_path','schema'].includes(String(assertion.type))">路径<input v-model="assertion.path" placeholder="$.data" @input="publish" /></label>
          <label v-if="assertion.type === 'header'">响应头<input v-model="assertion.name" placeholder="Content-Type" @input="publish" /></label>
          <label v-if="!['exists','not_exists'].includes(String(assertion.operator))">期望值<input :data-testid="`assertion-expected-${index}`" :value="renderValue(assertion.expected)" @input="updateExpected(assertion, ($event.target as HTMLInputElement).value)" /></label>
          <label>超时(ms)<input v-model.number="assertion.timeout_ms" type="number" min="0" max="60000" @input="publish" /></label>
          <label class="toggle-line"><input v-model="assertion.enabled" type="checkbox" @change="publish" />启用</label>
          <button class="mini-icon danger" type="button" title="删除断言" @click="local.assertions.splice(index, 1); publish()"><Trash2 :size="15" /></button>
          <small v-for="([field, message]) in validationMessages(`assertions[${index}]`, validationErrors)" :key="field" :data-error-for="field" class="field-error row-feedback">{{ message }}</small>
          <small v-for="([field, message]) in validationMessages(`assertions[${index}]`, validationWarnings)" :key="field" :data-warning-for="field" class="field-warning row-feedback">{{ message }}</small>
        </div>
      </section>

      <section class="editor-section"><div class="section-heading"><strong>提取变量</strong><button class="mini-icon" type="button" title="增加提取" @click="addExtraction"><Plus :size="15" /></button></div>
        <div v-for="(extraction, index) in local.extractions" :key="index" class="extraction-row"><label>变量名<input v-model="extraction.target" @input="publish" /></label><label>来源<select v-model="extraction.type" @change="publish"><option value="json_path">JSON Path</option><option value="header">响应头</option><option value="cookie">Cookie</option><option value="status_code">状态码</option></select></label><label v-if="extraction.type === 'json_path'">路径<input v-model="extraction.path" @input="publish" /></label><label v-else-if="['header','cookie'].includes(String(extraction.type))">名称<input v-model="extraction.name" @input="publish" /></label><label class="toggle-line"><input v-model="extraction.required" type="checkbox" @change="publish" />必需</label><button class="mini-icon danger" type="button" title="删除提取" @click="local.extractions.splice(index, 1); publish()"><Trash2 :size="14" /></button><small v-for="([field, message]) in validationMessages(`extractions[${index}]`, validationErrors)" :key="field" :data-error-for="field" class="field-error row-feedback">{{ message }}</small><small v-for="([field, message]) in validationMessages(`extractions[${index}]`, validationWarnings)" :key="field" :data-warning-for="field" class="field-warning row-feedback">{{ message }}</small></div>
      </section>

      <details class="advanced-editor"><summary>依赖与前后置处理</summary>
        <section class="editor-section"><div class="section-heading"><strong>用例依赖</strong><button class="mini-icon" type="button" title="添加依赖" @click="addDependency"><Plus :size="14" /></button></div><div v-for="(dependency, index) in local.dependencies" :key="index" class="dependency-row"><label>依赖用例版本<input v-model="dependency.case_version_id" @input="publish" /></label><label>导出变量<input :value="(dependency.exports as string[] || []).join(', ')" @input="updateExports(dependency, ($event.target as HTMLInputElement).value)" /></label><label class="toggle-line"><input v-model="dependency.required" type="checkbox" @change="publish" />必需</label><button class="mini-icon danger" type="button" title="删除依赖" @click="local.dependencies.splice(index, 1); publish()"><Trash2 :size="14" /></button><small v-for="([field, message]) in validationMessages(`dependencies[${index}]`, validationErrors)" :key="field" :data-error-for="field" class="field-error row-feedback">{{ message }}</small><small v-for="([field, message]) in validationMessages(`dependencies[${index}]`, validationWarnings)" :key="field" :data-warning-for="field" class="field-warning row-feedback">{{ message }}</small></div></section>
        <section v-for="phase in (['pre','post'] as ProcessingPhase[])" :key="phase" class="editor-section"><div class="section-heading"><strong>{{ phase === 'pre' ? '前置处理' : '后置处理' }}</strong><button class="mini-icon" type="button" title="添加处理" @click="addProcessing(phase)"><Plus :size="14" /></button></div><div v-for="(action, index) in local.processing[phase]" :key="index" class="processing-row"><select v-model="action.action" aria-label="处理动作" @change="changeProcessing(action)"><option value="set_variable">设置变量</option><option value="copy_variable">复制变量</option><option value="remove_variable">删除变量</option><option value="json_encode">JSON 编码</option><option value="json_decode">JSON 解码</option></select><input v-if="'name' in action" v-model="action.name" placeholder="变量名" @input="publish" /><input v-if="'source' in action" v-model="action.source" placeholder="来源变量" @input="publish" /><input v-if="'target' in action" v-model="action.target" placeholder="目标变量" @input="publish" /><input v-if="'value' in action" :value="renderValue(action.value)" placeholder="值" @input="action.value = parseScalar(($event.target as HTMLInputElement).value); publish()" /><button class="mini-icon danger" type="button" title="删除处理" @click="local.processing[phase].splice(index, 1); publish()"><Trash2 :size="14" /></button><small v-for="([field, message]) in validationMessages(`processing.${phase}[${index}]`, validationErrors)" :key="field" :data-error-for="field" class="field-error row-feedback">{{ message }}</small><small v-for="([field, message]) in validationMessages(`processing.${phase}[${index}]`, validationWarnings)" :key="field" :data-warning-for="field" class="field-warning row-feedback">{{ message }}</small></div></section>
      </details>
    </div>

    <div v-else class="raw-editor"><textarea v-model="raw" rows="26" spellcheck="false" @blur="applyRaw" /><p v-if="rawError" class="field-error">{{ rawError }}</p></div>
    <footer class="editor-footer"><span role="status">{{ savedMessage }}</span><button class="primary-command" type="button" :disabled="saving || hasBlockingError" @click="emit('save')">{{ saving ? '保存中...' : '保存草稿' }}</button></footer>
  </section>
</template>
