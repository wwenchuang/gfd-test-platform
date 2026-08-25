<script setup lang="ts">
import { Plus, Trash2 } from 'lucide-vue-next'

const props = withDefaults(defineProps<{
  modelValue: Array<Record<string, unknown>>
  errors?: Record<string, string>
  warnings?: Record<string, string>
  prefix?: string
  testIdPrefix?: string
}>(), { errors: () => ({}), warnings: () => ({}), prefix: 'assertions', testIdPrefix: '' })

const emit = defineEmits<{ 'update:modelValue': [assertions: Array<Record<string, unknown>>] }>()
const operators: Record<string, string[]> = {
  status_code: ['equals', 'not_equals', 'in'],
  json_path: ['equals', 'not_equals', 'contains', 'not_contains', 'exists', 'not_exists', 'greater_than', 'less_than', 'matches', 'in'],
  header: ['equals', 'not_equals', 'contains', 'not_contains', 'exists', 'not_exists', 'matches', 'in'],
  response_time: ['greater_than', 'less_than'],
  schema: ['equals'],
}

function clone(): Array<Record<string, unknown>> {
  return JSON.parse(JSON.stringify(props.modelValue)) as Array<Record<string, unknown>>
}

function testId(value: string): string {
  return props.testIdPrefix ? `${props.testIdPrefix}-${value}` : value
}

function fieldPath(index: number): string {
  return `${props.prefix}[${index}]`
}

function messages(index: number, source: Record<string, string>): Array<[string, string]> {
  const prefix = fieldPath(index)
  return Object.entries(source).filter(([field]) => field === prefix || field.startsWith(`${prefix}.`))
}

function patch(index: number, field: string, value: unknown): void {
  const assertions = clone()
  assertions[index][field] = value
  emit('update:modelValue', assertions)
}

function changeType(index: number, type: string): void {
  const assertions = clone()
  const assertion = assertions[index]
  assertion.type = type
  const allowed = operators[type] || ['equals']
  if (!allowed.includes(String(assertion.operator))) assertion.operator = allowed[0]
  if (type === 'response_time' && typeof assertion.expected !== 'number') assertion.expected = 1000
  if (type === 'status_code' && typeof assertion.expected !== 'number') assertion.expected = 200
  emit('update:modelValue', assertions)
}

function updateExpected(index: number, value: string): void {
  const assertion = props.modelValue[index]
  const expected = assertion.type === 'status_code' && assertion.operator !== 'in'
    ? numericOrText(value)
    : parseScalar(value)
  patch(index, 'expected', expected)
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
</script>

<template>
  <section class="editor-section assertion-list-editor">
    <div class="section-heading"><strong>业务断言</strong><button :data-testid="testId('add-assertion')" class="mini-icon" type="button" title="增加断言" @click="emit('update:modelValue', [...clone(), { type: 'status_code', operator: 'equals', expected: 200, timeout_ms: 0, enabled: true }])"><Plus :size="15" /></button></div>
    <p v-if="!modelValue.length" class="compact-empty">尚未配置业务断言。</p>
    <div v-for="(assertion, index) in modelValue" :key="index" class="assertion-card">
      <label>类型<select :value="assertion.type" @change="changeType(index, ($event.target as HTMLSelectElement).value)"><option value="status_code">HTTP 状态码</option><option value="json_path">响应 JSON 字段</option><option value="header">响应头</option><option value="response_time">响应时间</option><option value="schema">响应结构</option></select></label>
      <label>比较<select :value="assertion.operator" @change="patch(index, 'operator', ($event.target as HTMLSelectElement).value)"><option v-for="operator in operators[String(assertion.type)] || ['equals']" :key="operator" :value="operator">{{ operator }}</option></select></label>
      <label v-if="['json_path','schema'].includes(String(assertion.type))">路径<input :value="String(assertion.path || '')" :placeholder="assertion.type === 'json_path' ? '$.code' : '$.data'" @input="patch(index, 'path', ($event.target as HTMLInputElement).value)" /></label>
      <label v-if="assertion.type === 'header'">响应头<input :value="String(assertion.name || '')" placeholder="Content-Type" @input="patch(index, 'name', ($event.target as HTMLInputElement).value)" /></label>
      <label v-if="!['exists','not_exists'].includes(String(assertion.operator))">期望值<input :data-testid="testId(`assertion-expected-${index}`)" :value="renderValue(assertion.expected)" @input="updateExpected(index, ($event.target as HTMLInputElement).value)" /></label>
      <label>超时(ms)<input :value="Number(assertion.timeout_ms || 0)" type="number" min="0" max="60000" @input="patch(index, 'timeout_ms', Number(($event.target as HTMLInputElement).value))" /></label>
      <label class="toggle-line"><input :checked="assertion.enabled !== false" type="checkbox" @change="patch(index, 'enabled', ($event.target as HTMLInputElement).checked)" />启用</label>
      <button class="mini-icon danger" type="button" title="删除断言" @click="emit('update:modelValue', clone().filter((_, row) => row !== index))"><Trash2 :size="15" /></button>
      <small v-for="([field, message]) in messages(index, errors)" :key="field" :data-error-for="field" class="field-error row-feedback">{{ message }}</small>
      <small v-for="([field, message]) in messages(index, warnings)" :key="field" :data-warning-for="field" class="field-warning row-feedback">{{ message }}</small>
    </div>
  </section>
</template>
