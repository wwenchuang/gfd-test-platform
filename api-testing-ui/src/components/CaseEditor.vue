<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { Code2, List, Plus, Trash2 } from 'lucide-vue-next'

import type { CaseDraft } from '../api/contracts'

const props = withDefaults(defineProps<{ modelValue: CaseDraft; validationErrors?: Record<string, string>; saving?: boolean; savedMessage?: string }>(), {
  validationErrors: () => ({}), saving: false, savedMessage: '',
})
const emit = defineEmits<{ 'update:modelValue': [draft: CaseDraft]; save: [] }>()
const mode = ref<'structured' | 'raw'>('structured')
const local = ref<CaseDraft>(clone(props.modelValue))
const raw = ref(JSON.stringify(local.value, null, 2))
const rawError = ref('')

watch(() => props.modelValue, value => {
  if (JSON.stringify(value) !== JSON.stringify(local.value)) {
    local.value = clone(value)
    raw.value = JSON.stringify(value, null, 2)
  }
}, { deep: true })

const mapSections = computed(() => [
  ['请求头', 'headers'], ['查询参数', 'query'], ['路径参数', 'path_params'], ['Cookie', 'cookies'],
] as const)

function publish(): void {
  raw.value = JSON.stringify(local.value, null, 2)
  emit('update:modelValue', clone(local.value))
}
function updateMap(field: 'headers' | 'query' | 'path_params' | 'cookies', value: string): void {
  try {
    const parsed = JSON.parse(value)
    if (!parsed || Array.isArray(parsed) || typeof parsed !== 'object') return
    local.value.request[field] = parsed
    publish()
  } catch { /* keep editing until valid JSON */ }
}
function updateJson(field: 'body' | 'data_rows' | 'extractions' | 'assertions', value: string): void {
  try {
    ;(local.value as unknown as Record<string, unknown>)[field] = JSON.parse(value)
    publish()
  } catch { /* keep editing until valid JSON */ }
}
function applyRaw(): void {
  try {
    local.value = JSON.parse(raw.value) as CaseDraft
    rawError.value = ''
    publish()
  } catch { rawError.value = 'JSON 格式不正确' }
}
function addAssertion(): void {
  local.value.assertions.push({ type: 'status_code', operator: 'equals', expected: 200, timeout_ms: 0, enabled: true })
  publish()
}
function updateExpected(assertion: Record<string, unknown>, value: string): void {
  const numeric = Number(value)
  assertion.expected = ['status_code', 'response_time'].includes(String(assertion.type)) && value.trim() !== '' && Number.isFinite(numeric)
    ? numeric : value
  publish()
}

function clone(value: CaseDraft): CaseDraft {
  return JSON.parse(JSON.stringify(value)) as CaseDraft
}
</script>

<template>
  <section class="case-editor">
    <header class="editor-toolbar"><div><h2>测试用例</h2><span>{{ savedMessage || '修改后先保存，再调试' }}</span></div><div class="segmented"><button data-testid="structured-tab" type="button" :class="{ active: mode === 'structured' }" @click="mode = 'structured'"><List :size="15" />结构化</button><button data-testid="raw-tab" type="button" :class="{ active: mode === 'raw' }" @click="mode = 'raw'"><Code2 :size="15" />原始 JSON</button></div></header>
    <div v-if="mode === 'structured'" class="editor-form">
      <div class="form-grid"><label>用例名称<input v-model="local.name" data-testid="case-name" @input="publish" /></label><label>优先级<select v-model="local.priority" @change="publish"><option v-for="priority in ['P0','P1','P2','P3']" :key="priority">{{ priority }}</option></select></label></div>
      <label>测试目的<textarea v-model="local.purpose" rows="2" @input="publish" /></label>
      <fieldset><legend>请求</legend><div class="form-grid request-line"><label>方法<input v-model="local.request.method" @input="publish" /></label><label class="grow">路径<input v-model="local.request.path" @input="publish" /><small v-if="validationErrors['request.path']" data-error-for="request.path" class="field-error">{{ validationErrors['request.path'] }}</small></label><label>服务<input v-model="local.request.service" @input="publish" /></label></div></fieldset>
      <div class="structured-grid"><label v-for="[label, field] in mapSections" :key="field">{{ label }}<textarea :value="JSON.stringify(local.request[field], null, 2)" rows="4" @input="updateMap(field, ($event.target as HTMLTextAreaElement).value)" /></label></div>
      <label>请求体<textarea :value="JSON.stringify(local.request.body, null, 2)" rows="5" @input="updateJson('body', ($event.target as HTMLTextAreaElement).value)" /></label>
      <div class="structured-grid"><label>数据行<textarea :value="JSON.stringify(local.data_rows, null, 2)" rows="6" @input="updateJson('data_rows', ($event.target as HTMLTextAreaElement).value)" /></label><label>提取变量<textarea :value="JSON.stringify(local.extractions, null, 2)" rows="6" @input="updateJson('extractions', ($event.target as HTMLTextAreaElement).value)" /></label></div>
      <div class="section-heading"><strong>断言</strong><button class="mini-icon" type="button" title="增加断言" @click="addAssertion"><Plus :size="15" /></button></div>
      <div v-for="(assertion, index) in local.assertions" :key="index" class="assertion-row"><select v-model="assertion.type" @change="publish"><option value="status_code">状态码</option><option value="json_path">JSON Path</option><option value="header">响应头</option><option value="response_time">响应时间</option><option value="schema">Schema</option></select><select v-model="assertion.operator" @change="publish"><option value="equals">等于</option><option value="contains">包含</option><option value="exists">存在</option><option value="less_than">小于</option></select><input :data-testid="`assertion-expected-${index}`" :value="String(assertion.expected ?? '')" @input="updateExpected(assertion, ($event.target as HTMLInputElement).value)" /><button class="mini-icon danger" type="button" title="删除断言" @click="local.assertions.splice(index, 1); publish()"><Trash2 :size="15" /></button></div>
      <details><summary>高级处理与依赖</summary><pre>{{ JSON.stringify({ dependencies: local.dependencies, processing: local.processing }, null, 2) }}</pre></details>
    </div>
    <div v-else class="raw-editor"><textarea v-model="raw" rows="26" spellcheck="false" @blur="applyRaw" /><p v-if="rawError" class="field-error">{{ rawError }}</p></div>
    <footer class="editor-footer"><span role="status">{{ savedMessage }}</span><button class="primary-command" type="button" :disabled="saving" @click="$emit('save')">{{ saving ? '保存中...' : '保存草稿' }}</button></footer>
  </section>
</template>
