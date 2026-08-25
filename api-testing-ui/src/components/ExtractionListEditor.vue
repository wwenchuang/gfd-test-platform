<script setup lang="ts">
import { Plus, Trash2 } from 'lucide-vue-next'

const props = withDefaults(defineProps<{
  modelValue: Array<Record<string, unknown>>
  errors?: Record<string, string>
  warnings?: Record<string, string>
  prefix?: string
  testIdPrefix?: string
}>(), { errors: () => ({}), warnings: () => ({}), prefix: 'extractions', testIdPrefix: '' })

const emit = defineEmits<{ 'update:modelValue': [extractions: Array<Record<string, unknown>>] }>()

function clone(): Array<Record<string, unknown>> {
  return JSON.parse(JSON.stringify(props.modelValue)) as Array<Record<string, unknown>>
}

function testId(value: string): string {
  return props.testIdPrefix ? `${props.testIdPrefix}-${value}` : value
}

function messages(index: number, source: Record<string, string>): Array<[string, string]> {
  const prefix = `${props.prefix}[${index}]`
  return Object.entries(source).filter(([field]) => field === prefix || field.startsWith(`${prefix}.`))
}

function patch(index: number, field: string, value: unknown): void {
  const extractions = clone()
  extractions[index][field] = value
  emit('update:modelValue', extractions)
}
</script>

<template>
  <section class="editor-section extraction-list-editor">
    <div class="section-heading"><strong>输出变量</strong><button :data-testid="testId('add-extraction')" class="mini-icon" type="button" title="增加提取" @click="emit('update:modelValue', [...clone(), { target: `变量${modelValue.length + 1}`, type: 'json_path', path: '$.data', required: true }])"><Plus :size="15" /></button></div>
    <p v-if="!modelValue.length" class="compact-empty">当前步骤不输出变量。</p>
    <div v-for="(extraction, index) in modelValue" :key="index" class="extraction-row">
      <label>变量名<input :value="String(extraction.target || '')" @input="patch(index, 'target', ($event.target as HTMLInputElement).value)" /></label>
      <label>来源<select :value="extraction.type" @change="patch(index, 'type', ($event.target as HTMLSelectElement).value)"><option value="json_path">JSON Path</option><option value="header">响应头</option><option value="cookie">Cookie</option><option value="status_code">状态码</option></select></label>
      <label v-if="extraction.type === 'json_path'">路径<input :value="String(extraction.path || '')" @input="patch(index, 'path', ($event.target as HTMLInputElement).value)" /></label>
      <label v-else-if="['header','cookie'].includes(String(extraction.type))">名称<input :value="String(extraction.name || '')" @input="patch(index, 'name', ($event.target as HTMLInputElement).value)" /></label>
      <label class="toggle-line"><input :checked="extraction.required !== false" type="checkbox" @change="patch(index, 'required', ($event.target as HTMLInputElement).checked)" />必需</label>
      <button class="mini-icon danger" type="button" title="删除提取" @click="emit('update:modelValue', clone().filter((_, row) => row !== index))"><Trash2 :size="14" /></button>
      <small v-for="([field, message]) in messages(index, errors)" :key="field" :data-error-for="field" class="field-error row-feedback">{{ message }}</small>
      <small v-for="([field, message]) in messages(index, warnings)" :key="field" :data-warning-for="field" class="field-warning row-feedback">{{ message }}</small>
    </div>
  </section>
</template>
