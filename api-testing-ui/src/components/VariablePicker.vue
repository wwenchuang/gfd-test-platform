<script setup lang="ts">
import { computed, ref } from 'vue'
import { Search, X } from 'lucide-vue-next'

import type { WorkflowVariableOption } from '../api/contracts'

const props = defineProps<{
  modelValue: string[]
  options: WorkflowVariableOption[]
  testIdPrefix?: string
}>()
const emit = defineEmits<{ 'update:modelValue': [value: string[]] }>()
const query = ref('')
const filtered = computed(() => {
  const keyword = query.value.trim().toLocaleLowerCase()
  if (!keyword) return props.options
  return props.options.filter(option => `${option.name} ${option.source}`.toLocaleLowerCase().includes(keyword))
})

function selected(name: string): boolean {
  return props.modelValue.includes(name)
}

function toggle(name: string): void {
  const next = new Set(props.modelValue)
  if (next.has(name)) next.delete(name)
  else next.add(name)
  emit('update:modelValue', [...next])
}

function id(value: string): string {
  return props.testIdPrefix ? `${props.testIdPrefix}-${value}` : value
}
</script>

<template>
  <details class="variable-picker">
    <summary><span>{{ modelValue.length ? `已选 ${modelValue.length} 个变量` : '选择已有变量' }}</span><small>{{ options.filter(item => item.available).length }} 个可用</small></summary>
    <div class="variable-picker-panel">
      <div v-if="modelValue.length" class="variable-tags">
      <button v-for="name in modelValue" :key="name" type="button" class="variable-tag" :class="{ missing: !options.find(item => item.name === name)?.available }" :title="`移除变量 ${name}`" @click="toggle(name)">
        <span>{{ name }}</span><X :size="12" />
      </button>
      </div>
      <label class="picker-search"><Search :size="15" /><input :data-testid="id('variable-search')" v-model="query" placeholder="搜索变量名或来源" /></label>
      <div class="variable-options">
        <button
          v-for="option in filtered"
          :key="option.name"
          :data-testid="id(`variable-option-${option.name}`)"
          type="button"
          class="variable-option"
          :class="{ selected: selected(option.name), missing: !option.available }"
          @click="toggle(option.name)"
        >
          <span><strong>{{ option.name }}</strong><small>{{ option.source }}</small></span>
          <span class="variable-state">{{ selected(option.name) ? '已选' : option.available ? '选择' : '未找到来源' }}</span>
        </button>
        <p v-if="!filtered.length" class="compact-empty">没有匹配的变量。</p>
      </div>
    </div>
  </details>
</template>
