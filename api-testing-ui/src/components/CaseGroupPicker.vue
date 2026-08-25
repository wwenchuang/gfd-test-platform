<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref } from 'vue'
import { FolderInput, Plus, Search, X } from 'lucide-vue-next'

const props = withDefaults(defineProps<{
  groups: string[]
  currentGroup?: string
  title?: string
}>(), {
  currentGroup: '',
  title: '移动分组',
})

const emit = defineEmits<{ select: [groupName: string]; close: [] }>()
const root = ref<HTMLElement | null>(null)
const searchInput = ref<HTMLInputElement | null>(null)
const query = ref('')
const normalizedQuery = computed(() => query.value.trim().toLocaleLowerCase())
const filteredGroups = computed(() => props.groups.filter(group => (
  !normalizedQuery.value || group.toLocaleLowerCase().includes(normalizedQuery.value)
)))
const canCreate = computed(() => {
  const value = query.value.trim()
  return Boolean(value) && !props.groups.some(group => group.toLocaleLowerCase() === value.toLocaleLowerCase())
})

function choose(groupName: string): void {
  emit('select', groupName)
}

function handleKeydown(event: KeyboardEvent): void {
  if (event.key === 'Escape') emit('close')
}

function handleOutsideClick(event: MouseEvent): void {
  if (root.value && !root.value.contains(event.target as Node)) emit('close')
}

onMounted(() => {
  document.addEventListener('mousedown', handleOutsideClick)
  void nextTick(() => searchInput.value?.focus())
})
onBeforeUnmount(() => document.removeEventListener('mousedown', handleOutsideClick))
</script>

<template>
  <div ref="root" data-testid="case-group-picker" class="case-group-picker" tabindex="-1" @keydown="handleKeydown">
    <header>
      <strong>{{ title }}</strong>
      <button class="mini-icon" type="button" title="关闭分组选择" @click="emit('close')"><X :size="14" /></button>
    </header>
    <label class="search-box case-group-picker-search">
      <Search :size="14" />
      <span class="sr-only">搜索或创建分组</span>
      <input ref="searchInput" v-model="query" data-testid="case-group-picker-search" placeholder="搜索或输入新分组" />
    </label>
    <small>{{ filteredGroups.length }} 个匹配分组</small>
    <div class="case-group-picker-options">
      <button
        v-for="group in filteredGroups"
        :key="group"
        :data-testid="`case-group-picker-option-${group}`"
        type="button"
        :class="{ active: group === currentGroup }"
        :title="group"
        @click="choose(group)"
      >
        <FolderInput :size="14" /><span>{{ group }}</span>
      </button>
      <button v-if="canCreate" data-testid="case-group-picker-create" class="create" type="button" @click="choose(query.trim())">
        <Plus :size="14" /><span>创建并移动到“{{ query.trim() }}”</span>
      </button>
      <p v-if="!filteredGroups.length && !canCreate">没有匹配分组</p>
    </div>
  </div>
</template>
