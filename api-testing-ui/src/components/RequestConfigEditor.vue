<script setup lang="ts">
import { nextTick, ref, watch } from 'vue'
import { Braces, Plus, Trash2 } from 'lucide-vue-next'

import type { CaseRequest, WorkflowVariableOption } from '../api/contracts'

type RequestMapField = 'headers' | 'query' | 'path_params' | 'cookies'

const props = withDefaults(defineProps<{
  modelValue: CaseRequest
  errors?: Record<string, string>
  warnings?: Record<string, string>
  prefix?: string
  testIdPrefix?: string
  variableOptions?: WorkflowVariableOption[]
}>(), { errors: () => ({}), warnings: () => ({}), prefix: 'request', testIdPrefix: '', variableOptions: () => [] })

const emit = defineEmits<{
  'update:modelValue': [request: CaseRequest]
  validity: [valid: boolean]
}>()

const sections = [
  { label: '请求头', field: 'headers' as const },
  { label: '查询参数', field: 'query' as const },
  { label: '路径参数', field: 'path_params' as const },
  { label: 'Cookie', field: 'cookies' as const },
]
const local = ref(cloneRequest(props.modelValue))
const valueTypeHints = ref(requestMapTypeHints(props.modelValue))
const bodyText = ref(JSON.stringify(props.modelValue.body, null, 2))
const bodyError = ref('')
const bodyInput = ref<HTMLTextAreaElement | null>(null)
const variableTarget = ref<{ field: RequestMapField; index: number } | { field: 'body' } | null>(null)
const pending: Record<RequestMapField, Set<string>> = {
  headers: new Set(), query: new Set(), path_params: new Set(), cookies: new Set(),
}

watch(() => props.modelValue, value => {
  if (JSON.stringify(value) === JSON.stringify(local.value)) return
  local.value = cloneRequest(value)
  valueTypeHints.value = requestMapTypeHints(value)
  bodyText.value = JSON.stringify(value.body, null, 2)
  bodyError.value = ''
  for (const names of Object.values(pending)) names.clear()
  emit('validity', true)
}, { deep: true })

function cloneRequest(value: CaseRequest): CaseRequest {
  return JSON.parse(JSON.stringify(value)) as CaseRequest
}

function requestMapTypeHints(value: CaseRequest): Record<RequestMapField, Record<string, unknown>> {
  return {
    headers: cloneRecord(value.headers),
    query: cloneRecord(value.query),
    path_params: cloneRecord(value.path_params),
    cookies: cloneRecord(value.cookies),
  }
}

function cloneRecord(value: Record<string, unknown>): Record<string, unknown> {
  return JSON.parse(JSON.stringify(value)) as Record<string, unknown>
}

function testId(value: string): string {
  return props.testIdPrefix ? `${props.testIdPrefix}-${value}` : value
}

function path(field?: string): string {
  return [props.prefix, field].filter(Boolean).join('.')
}

function messages(field: string, source: Record<string, string>): Array<[string, string]> {
  const target = path(field)
  return Object.entries(source).filter(([name]) => name === target || name.startsWith(`${target}.`))
}

function missingMessages(field: RequestMapField, source: Record<string, string>): Array<[string, string]> {
  const target = `${path(field)}.`
  const existing = new Set(Object.keys(local.value[field]))
  return Object.entries(source).filter(([name]) => {
    if (!name.startsWith(target)) return false
    return !existing.has(name.slice(target.length).split('.')[0])
  })
}

function publish(): void {
  const request = cloneRequest(local.value)
  for (const field of Object.keys(pending) as RequestMapField[]) {
    for (const name of pending[field]) delete request[field][name]
  }
  emit('update:modelValue', request)
}

function addEntry(field: RequestMapField): void {
  const target = local.value[field]
  let name = '新参数'
  let index = 2
  while (Object.prototype.hasOwnProperty.call(target, name)) name = `新参数${index++}`
  target[name] = ''
  valueTypeHints.value[field][name] = ''
  pending[field].add(name)
}

function renameEntry(field: RequestMapField, previous: string, nextValue: string): void {
  const next = nextValue.trim()
  const target = local.value[field]
  if (!next || (next !== previous && Object.prototype.hasOwnProperty.call(target, next))) return
  const rebuilt: Record<string, unknown> = {}
  for (const [name, value] of Object.entries(target)) rebuilt[name === previous ? next : name] = value
  local.value[field] = rebuilt
  if (next !== previous) {
    valueTypeHints.value[field][next] = valueTypeHints.value[field][previous]
    delete valueTypeHints.value[field][previous]
  }
  if (pending[field].delete(previous)) pending[field].delete(next)
  publish()
}

function updateValue(field: RequestMapField, index: number, value: string): void {
  const name = Object.keys(local.value[field])[index]
  if (!name) return
  local.value[field][name] = preserveValueType(valueTypeHints.value[field][name], value)
  publish()
}

function preserveValueType(current: unknown, value: string): unknown {
  if (typeof current === 'number') {
    const parsed = Number(value)
    return value.trim() && Number.isFinite(parsed) ? parsed : value
  }
  if (typeof current === 'boolean') {
    if (value.trim().toLowerCase() === 'true') return true
    if (value.trim().toLowerCase() === 'false') return false
    return value
  }
  if (Array.isArray(current) || (current !== null && typeof current === 'object')) {
    try {
      const parsed = JSON.parse(value) as unknown
      if (Array.isArray(current) && Array.isArray(parsed)) return parsed
      if (!Array.isArray(current) && parsed !== null && typeof parsed === 'object' && !Array.isArray(parsed)) return parsed
    } catch {
      // Keep unfinished structured values editable until they become valid JSON.
    }
  }
  return value
}

function removeEntry(field: RequestMapField, name: string): void {
  pending[field].delete(name)
  delete local.value[field][name]
  delete valueTypeHints.value[field][name]
  publish()
}

function updateBody(value: string): void {
  bodyText.value = value
  try {
    local.value.body = JSON.parse(value)
    bodyError.value = ''
    emit('validity', true)
    publish()
  } catch {
    bodyError.value = '请求体 JSON 格式不正确'
    emit('validity', false)
  }
}

function toggleVariableTarget(field: RequestMapField, index: number): void {
  variableTarget.value = variableTarget.value?.field === field && 'index' in variableTarget.value && variableTarget.value.index === index
    ? null
    : { field, index }
}

function insertVariable(name: string): void {
  const target = variableTarget.value
  if (!target) return
  const template = `{{${name}}}`
  if (target.field === 'body') {
    const start = bodyInput.value?.selectionStart ?? bodyText.value.length
    const end = bodyInput.value?.selectionEnd ?? start
    updateBody(`${bodyText.value.slice(0, start)}${template}${bodyText.value.slice(end)}`)
    void nextTick(() => {
      bodyInput.value?.focus()
      bodyInput.value?.setSelectionRange(start + template.length, start + template.length)
    })
  } else {
    const key = Object.keys(local.value[target.field])[target.index]
    if (!key) return
    const current = renderValue(local.value[target.field][key])
    local.value[target.field][key] = `${current}${template}`
    publish()
  }
  variableTarget.value = null
}

function renderValue(value: unknown): string {
  return typeof value === 'string' ? value : JSON.stringify(value)
}
</script>

<template>
  <div class="request-config-editor">
    <fieldset>
      <legend>请求配置</legend>
      <div class="form-grid request-line">
        <label>方法<select :value="local.method" @change="local.method = ($event.target as HTMLSelectElement).value; publish()"><option v-for="method in ['GET','POST','PUT','PATCH','DELETE','HEAD','OPTIONS']" :key="method">{{ method }}</option></select></label>
        <label class="grow">路径<input :value="local.path" @input="local.path = ($event.target as HTMLInputElement).value; publish()" /><small v-for="([field, message]) in messages('path', errors)" :key="field" :data-error-for="field" class="field-error">{{ message }}</small></label>
        <label>服务<input :value="local.service" @input="local.service = ($event.target as HTMLInputElement).value; publish()" /></label>
      </div>
    </fieldset>
    <div class="structured-grid">
      <fieldset v-for="section in sections" :key="section.field" class="row-editor">
        <legend>{{ section.label }}</legend>
        <div v-for="([name, value], index) in Object.entries(local[section.field])" :key="`${section.field}-${index}`" class="key-value-row">
          <input :data-testid="testId(`${section.field}-name`)" :value="name" aria-label="参数名" @change="renameEntry(section.field, name, ($event.target as HTMLInputElement).value)" />
          <input :data-testid="testId(`${section.field}-value`)" :value="renderValue(value)" aria-label="参数值" @input="updateValue(section.field, index, ($event.target as HTMLInputElement).value)" />
          <button v-if="variableOptions.length" :data-testid="testId(`${section.field}-variable-${index}`)" class="mini-icon" type="button" title="插入已有变量" @click="toggleVariableTarget(section.field, index)"><Braces :size="14" /></button>
          <button class="mini-icon danger" type="button" title="删除参数" @click="removeEntry(section.field, name)"><Trash2 :size="14" /></button>
          <div v-if="variableTarget?.field === section.field && 'index' in variableTarget && variableTarget.index === index" class="variable-insert-menu">
            <strong>插入已有变量</strong>
            <button v-for="option in variableOptions" :key="option.name" :data-testid="`variable-insert-${option.name}`" type="button" :disabled="!option.available" @click="insertVariable(option.name)"><span>{{ option.name }}</span><small>{{ option.source }}</small></button>
          </div>
          <small v-for="([field, message]) in messages(`${section.field}.${name}`, errors)" :key="field" :data-error-for="field" class="field-error row-feedback">{{ message }}</small>
          <small v-for="([field, message]) in messages(`${section.field}.${name}`, warnings)" :key="field" :data-warning-for="field" class="field-warning row-feedback">{{ message }}</small>
        </div>
        <small v-for="([field, message]) in missingMessages(section.field, errors)" :key="field" :data-error-for="field" class="field-error">{{ message }}</small>
        <small v-for="([field, message]) in missingMessages(section.field, warnings)" :key="field" :data-warning-for="field" class="field-warning">{{ message }}</small>
        <button :data-testid="testId(`${section.field}-add`)" class="row-add" type="button" @click="addEntry(section.field)"><Plus :size="14" />添加参数</button>
      </fieldset>
    </div>
    <label class="request-body-field">请求体（JSON）
      <textarea ref="bodyInput" :data-testid="testId('request-body')" :value="bodyText" rows="6" @input="updateBody(($event.target as HTMLTextAreaElement).value)" />
      <button v-if="variableOptions.length" :data-testid="testId('body-variable')" class="variable-body-command" type="button" @click="variableTarget = variableTarget?.field === 'body' ? null : { field: 'body' }"><Braces :size="14" />插入变量</button>
      <div v-if="variableTarget?.field === 'body'" class="variable-insert-menu body-menu">
        <strong>请先将光标放在合法 JSON 字符串中，再插入变量</strong>
        <button v-for="option in variableOptions" :key="option.name" :data-testid="`variable-insert-${option.name}`" type="button" :disabled="!option.available" @click="insertVariable(option.name)"><span>{{ option.name }}</span><small>{{ option.source }}</small></button>
      </div>
      <small v-if="bodyError" :data-error-for="path('body')" class="field-error">{{ bodyError }}</small><template v-else><small v-for="([field, message]) in messages('body', errors)" :key="field" :data-error-for="field" class="field-error">{{ message }}</small></template>
    </label>
  </div>
</template>
