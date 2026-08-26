<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { Check, Eye, EyeOff, Search, X } from 'lucide-vue-next'

import type { WorkflowStepPreview, WorkflowStepPreviewField } from '../api/contracts'

const props = defineProps<{
  preview: WorkflowStepPreview
  extractions: Array<Record<string, unknown>>
  stepName: string
}>()
const emit = defineEmits<{
  apply: [payload: { extractions: Array<Record<string, unknown>>; overrides: Record<string, unknown> }]
  close: []
}>()

const query = ref('')
const selectedIds = ref<string[]>([])
const targets = ref<Record<string, string>>({})
const values = ref<Record<string, string>>({})
const revealed = ref<Record<string, boolean>>({})
const VARIABLE_NAME = /^[A-Za-z_][A-Za-z0-9_.-]*$/

const filteredFields = computed(() => {
  const keyword = query.value.trim().toLowerCase()
  if (!keyword) return props.preview.fields
  return props.preview.fields.filter(field => [
    field.path, field.name, field.suggested_target, renderValue(field.value), sourceLabel(field.source),
  ].some(value => String(value || '').toLowerCase().includes(keyword)))
})
const selectedFields = computed(() => props.preview.fields.filter(field => selectedIds.value.includes(field.id)))
const targetErrors = computed<Record<string, string>>(() => {
  const errors: Record<string, string> = {}
  const selectedLocators = new Set(selectedFields.value.map(field => field.id))
  const existingTargets = props.extractions
    .filter(extraction => !props.preview.fields.some(field => selectedLocators.has(field.id) && sameLocator(extraction, field)))
    .map(extraction => String(extraction.target || '').trim())
    .filter(Boolean)
  const counts = new Map<string, number>()
  for (const target of [...existingTargets, ...selectedFields.value.map(field => (targets.value[field.id] || '').trim()).filter(Boolean)]) {
    counts.set(target, (counts.get(target) || 0) + 1)
  }
  for (const field of selectedFields.value) {
    const target = (targets.value[field.id] || '').trim()
    if (!VARIABLE_NAME.test(target)) errors[field.id] = '变量名需以字母或下划线开头，仅包含字母、数字、点、横线或下划线'
    else if ((counts.get(target) || 0) > 1) errors[field.id] = '变量名重复，请为每个输出使用唯一名称'
  }
  return errors
})
const canApply = computed(() => selectedIds.value.length > 0 && !Object.keys(targetErrors.value).length)

watch(() => props.preview, preview => {
  selectedIds.value = []
  targets.value = Object.fromEntries(preview.fields.map(field => [field.id, existingTarget(field) || field.suggested_target]))
  values.value = Object.fromEntries(preview.fields.map(field => [field.id, renderValue(field.value)]))
  revealed.value = {}
  query.value = ''
}, { immediate: true })

function existingTarget(field: WorkflowStepPreviewField): string {
  const match = props.extractions.find(item => sameLocator(item, field))
  return String(match?.target || '')
}

function sameLocator(extraction: Record<string, unknown>, field: WorkflowStepPreviewField): boolean {
  if (String(extraction.type || '') !== field.source) return false
  return field.source === 'json_path'
    ? String(extraction.path || '') === String(field.path || '')
    : String(extraction.name || '') === field.name
}

function sourceLabel(source: WorkflowStepPreviewField['source']): string {
  return { json_path: 'JSON', header: '响应头', cookie: 'Cookie', status_code: '状态码' }[source]
}

function locator(field: WorkflowStepPreviewField): string {
  return field.path || field.name
}

function renderValue(value: unknown): string {
  if (typeof value === 'string') return value
  return JSON.stringify(value)
}

function parseEditedValue(field: WorkflowStepPreviewField): unknown {
  const value = values.value[field.id]
  if (field.value_type === 'string') return value
  try { return JSON.parse(value) } catch { return value }
}

function toggleSelected(id: string, selected: boolean): void {
  selectedIds.value = selected
    ? [...new Set([...selectedIds.value, id])]
    : selectedIds.value.filter(item => item !== id)
}

function apply(): void {
  if (!canApply.value) return
  const selected = props.preview.fields.filter(field => selectedIds.value.includes(field.id))
  const selectedIdsSet = new Set(selected.map(field => field.id))
  const extractions = props.extractions
    .filter(extraction => !props.preview.fields.some(field => selectedIdsSet.has(field.id) && sameLocator(extraction, field)))
    .map(item => ({ ...item }))
  const overrides: Record<string, unknown> = {}
  for (const field of selected) {
    const target = (targets.value[field.id] || field.suggested_target).trim()
    if (!target) continue
    const extraction: Record<string, unknown> = { target, type: field.source, required: true }
    if (field.source === 'json_path') extraction.path = field.path
    else if (field.source !== 'status_code') extraction.name = field.name
    extractions.push(extraction)
    if (values.value[field.id] !== renderValue(field.value)) {
      overrides[target] = parseEditedValue(field)
    }
  }
  emit('apply', { extractions, overrides })
}
</script>

<template>
  <aside class="workflow-preview-panel" aria-label="前置步骤响应选择">
    <header>
      <div>
        <strong>{{ stepName }} · 响应取值</strong>
        <span :class="['status-text', preview.status.toLowerCase()]">{{ preview.status === 'PASSED' ? '试运行通过' : '试运行未通过' }}</span>
      </div>
      <button class="mini-icon" type="button" title="关闭响应选择" @click="emit('close')"><X :size="15" /></button>
    </header>
    <p v-if="preview.error_message" class="inline-error">{{ preview.error_message }}</p>
    <p v-if="preview.missing_variables.length" class="field-error">缺少变量：{{ preview.missing_variables.join('、') }}</p>
    <div class="workflow-preview-summary">
      <span>已执行 {{ preview.trace.length }} 个步骤</span>
      <span v-if="preview.target_reached">已到达第 {{ preview.target_index + 1 }} 步</span>
      <span v-else-if="preview.executed_index !== null" class="field-warning">停在第 {{ preview.executed_index + 1 }} 步，目标步骤未执行</span>
      <span>可选 {{ preview.fields.length }} 个字段</span>
      <span v-if="preview.truncated" class="field-warning">响应较大，仅展示前 500 个字段</span>
    </div>
    <label class="picker-search workflow-preview-search"><Search :size="15" /><input v-model="query" data-testid="workflow-preview-search" placeholder="搜索字段、路径或响应值" /></label>
    <div class="workflow-preview-fields">
      <p v-if="!filteredFields.length" class="compact-empty">没有匹配的响应字段。</p>
      <article v-for="field in filteredFields" :key="field.id" class="workflow-preview-field">
        <input :data-testid="`workflow-preview-select-${field.id}`" type="checkbox" :checked="selectedIds.includes(field.id)" :aria-label="`选择 ${locator(field)}`" @change="toggleSelected(field.id, ($event.target as HTMLInputElement).checked)" />
        <div class="workflow-preview-locator"><span>{{ sourceLabel(field.source) }}<i v-if="field.sensitive">敏感</i></span><code>{{ locator(field) }}</code><small>{{ field.value_type }}</small></div>
        <label>变量名<input :data-testid="`workflow-preview-target-${field.id}`" v-model="targets[field.id]" /></label>
        <label class="workflow-preview-value">当前值
          <span>
            <input
              :data-testid="field.sensitive ? `workflow-preview-sensitive-${field.id}` : `workflow-preview-value-${field.id}`"
              v-model="values[field.id]"
              :type="field.sensitive && !revealed[field.id] ? 'password' : 'text'"
            />
            <button v-if="field.sensitive" :data-testid="`workflow-preview-reveal-${field.id}`" class="mini-icon" type="button" :title="revealed[field.id] ? '隐藏敏感值' : '显示敏感值'" @click="revealed[field.id] = !revealed[field.id]">
              <EyeOff v-if="revealed[field.id]" :size="14" /><Eye v-else :size="14" />
            </button>
          </span>
          <small v-if="values[field.id] !== renderValue(field.value)">本次预览替换，不写入用例</small>
        </label>
        <small v-if="targetErrors[field.id]" class="field-error workflow-preview-target-error">{{ targetErrors[field.id] }}</small>
      </article>
    </div>
    <footer>
      <small>勾选字段会生成输出变量；修改后的值仅用于继续预览后续步骤。</small>
      <button data-testid="workflow-preview-apply" class="primary-command" type="button" :disabled="!canApply" @click="apply"><Check :size="15" />应用选择</button>
    </footer>
  </aside>
</template>
