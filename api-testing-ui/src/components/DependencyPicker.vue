<script setup lang="ts">
import { computed, ref } from 'vue'
import { Search } from 'lucide-vue-next'

import type { CaseDependencyOption } from '../api/contracts'

const props = defineProps<{
  modelValue: string
  options: CaseDependencyOption[]
  disabledIds: string[]
}>()
const emit = defineEmits<{ 'update:modelValue': [value: string] }>()
const query = ref('')
const filteredGroups = computed(() => {
  const keyword = query.value.trim().toLocaleLowerCase()
  const groups = new Map<string, CaseDependencyOption[]>()
  for (const option of props.options) {
    const haystack = `${option.name} ${option.method} ${option.path} ${option.group} ${option.exports.join(' ')}`.toLocaleLowerCase()
    if (keyword && !haystack.includes(keyword)) continue
    const items = groups.get(option.group) || []
    items.push(option)
    groups.set(option.group, items)
  }
  return [...groups.entries()].map(([name, options]) => ({ name, options }))
})
const selected = computed(() => props.options.find(option => option.id === props.modelValue))

function choose(id: string): void {
  emit('update:modelValue', id)
}
</script>

<template>
  <details class="dependency-picker">
    <summary><span>{{ selected?.name || '选择已保存用例' }}</span><small>{{ selected ? `${selected.group} · v${selected.version}` : `${options.length} 个可选` }}</small></summary>
    <div class="dependency-picker-panel">
      <div v-if="selected" class="dependency-selected">
      <span><strong>{{ selected.name }}</strong><small>{{ selected.group }} · {{ selected.method }} {{ selected.path }} · v{{ selected.version }}</small></span>
      <button type="button" class="text-command" @click="choose('')">更换</button>
      </div>
      <label class="picker-search"><Search :size="15" /><input data-testid="dependency-search" v-model="query" placeholder="搜索用例、接口、路径或导出变量" /></label>
      <div class="dependency-options">
        <section v-for="group in filteredGroups" :key="group.name">
          <header>{{ group.name }}<span>{{ group.options.length }}</span></header>
          <button
            v-for="option in group.options"
            :key="option.id"
            :data-testid="`dependency-option-${option.id}`"
            type="button"
            class="dependency-option"
            :class="{ selected: option.id === modelValue }"
            :disabled="disabledIds.includes(option.id)"
            @click="choose(option.id)"
          >
            <span><strong>{{ option.name }}</strong><small>{{ option.method }} {{ option.path }} · v{{ option.version }}</small></span>
            <span>{{ option.exports.length ? `导出 ${option.exports.join('、')}` : '无导出变量' }}</span>
          </button>
        </section>
        <p v-if="!filteredGroups.length" class="compact-empty">没有匹配的已保存用例。</p>
      </div>
    </div>
  </details>
</template>
