<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { Search } from 'lucide-vue-next'

import type { ApiEndpoint, LoadState } from '../api/contracts'

const props = withDefaults(defineProps<{ endpoints: ApiEndpoint[]; selectedIds?: string[]; state?: LoadState; error?: string }>(), {
  selectedIds: () => [], state: 'ready', error: '',
})
const emit = defineEmits<{ 'selection-change': [ids: string[]]; activate: [endpoint: ApiEndpoint] }>()
const query = ref('')
const selected = ref(new Set(props.selectedIds))

watch(() => props.selectedIds, ids => { selected.value = new Set(ids) })

const filtered = computed(() => {
  const needle = query.value.trim().toLocaleLowerCase()
  if (!needle) return props.endpoints
  return props.endpoints.filter(item => [item.summary, item.path, item.method, ...item.tags].join(' ').toLocaleLowerCase().includes(needle))
})
const groups = computed(() => {
  const grouped = new Map<string, ApiEndpoint[]>()
  for (const endpoint of filtered.value) {
    const name = endpoint.tags[0] || '未分组接口'
    grouped.set(name, [...(grouped.get(name) || []), endpoint])
  }
  return [...grouped.entries()]
})

function toggle(endpointId: string, checked: boolean): void {
  const next = new Set(selected.value)
  if (checked) next.add(endpointId); else next.delete(endpointId)
  selected.value = next
  emit('selection-change', [...next])
}
</script>

<template>
  <section class="endpoint-tree" aria-label="接口范围">
    <header class="panel-header"><h2>接口范围</h2><span>{{ selected.size }} 已选</span></header>
    <label class="search-box"><Search :size="15" /><span class="sr-only">搜索接口</span><input v-model="query" data-testid="endpoint-search" placeholder="搜索名称或路径" /></label>
    <p v-if="state === 'loading'" class="state-message">正在读取接口...</p>
    <p v-else-if="state === 'failed'" class="state-message state-error">{{ error || '接口读取失败' }}</p>
    <p v-else-if="state === 'empty'" class="state-message">尚无已保存接口，请先导入接口来源。</p>
    <p v-if="state === 'partial'" class="partial-notice">部分接口未能读取，已展示可用结果。</p>
    <div v-for="[group, items] in groups" :key="group" class="endpoint-group">
      <h3>{{ group }} <span>{{ items.length }}</span></h3>
      <label v-for="endpoint in items" :key="endpoint.id" class="endpoint-row" @dblclick="emit('activate', endpoint)">
        <input
          :data-testid="`endpoint-${endpoint.id}`"
          type="checkbox"
          :checked="selected.has(endpoint.id)"
          @change="toggle(endpoint.id, ($event.target as HTMLInputElement).checked)"
        />
        <button type="button" class="endpoint-open" @click="emit('activate', endpoint)">
          <span :class="['method-badge', `method-${endpoint.method.toLowerCase()}`]">{{ endpoint.method }}</span>
          <span class="endpoint-copy"><strong>{{ endpoint.summary || endpoint.path }}</strong><small>{{ endpoint.path }}</small></span>
        </button>
      </label>
    </div>
    <p v-if="state === 'ready' && !filtered.length" class="state-message">没有匹配的接口。</p>
  </section>
</template>
