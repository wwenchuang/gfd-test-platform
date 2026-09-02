<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { ChevronDown, ChevronRight, Search, X } from 'lucide-vue-next'

import type { ApiEndpoint, LoadState } from '../api/contracts'
import { endpointGroupName, groupEndpoints } from '../utils/endpointGroups'

const props = withDefaults(defineProps<{ endpoints: ApiEndpoint[]; selectedIds?: string[]; state?: LoadState; error?: string; initialTab?: 'all' | 'selected' }>(), {
  selectedIds: () => [], state: 'ready', error: '', initialTab: 'all',
})
const emit = defineEmits<{ 'selection-change': [ids: string[]]; activate: [endpoint: ApiEndpoint] }>()
interface HighlightSegment {
  text: string
  match: boolean
}
interface EndpointDomain {
  name: string
  groups: Array<[string, ApiEndpoint[]]>
  items: ApiEndpoint[]
}

const query = ref('')
const searchComposing = ref(false)
const selected = ref(new Set(props.selectedIds))
const activeTab = ref<'all' | 'selected'>(props.initialTab)
const collapsedGroups = ref(new Set<string>())
const collapsedDomains = ref(new Set<string>())

watch(() => props.selectedIds, ids => {
  selected.value = new Set(ids)
  if (!ids.length && activeTab.value === 'selected') activeTab.value = 'all'
})

watch(() => props.initialTab, tab => { activeTab.value = tab })

function selectTab(tab: 'all' | 'selected'): void {
  activeTab.value = tab
}

function handleSearchCompositionStart(): void {
  searchComposing.value = true
}

function handleSearchInput(event: Event): void {
  const inputEvent = event as InputEvent
  if (searchComposing.value || inputEvent.isComposing) return
  query.value = (event.target as HTMLInputElement).value
}

function handleSearchCompositionEnd(event: CompositionEvent): void {
  searchComposing.value = false
  query.value = (event.target as HTMLInputElement).value
}

const filtered = computed(() => {
  const needle = query.value.trim().toLocaleLowerCase()
  if (!needle) return props.endpoints
  return props.endpoints.filter(item => [item.summary, item.path, item.method, endpointGroupName(item), ...item.tags].join(' ').toLocaleLowerCase().includes(needle))
})
const groups = computed(() => groupEndpoints(filtered.value))
const allGroupNames = computed(() => groupEndpoints(props.endpoints).map(([group]) => group))
const domains = computed(() => groupByDomain(groups.value))
const allDomains = computed(() => groupByDomain(groupEndpoints(props.endpoints)))
const useDomainHierarchy = computed(() => allGroupNames.value.length > 8)
const selectedEndpoints = computed(() => props.endpoints.filter(endpoint => selected.value.has(endpoint.id)))
const filteredSelectedEndpoints = computed(() => {
  const needle = query.value.trim().toLocaleLowerCase()
  if (!needle) return selectedEndpoints.value
  return selectedEndpoints.value.filter(item => matchesEndpoint(item, needle))
})
const additionalSearchMatches = computed(() => {
  if (activeTab.value !== 'selected' || !query.value.trim()) return 0
  return Math.max(0, filtered.value.length - filteredSelectedEndpoints.value.length)
})
const selectedGroups = computed(() => groupEndpoints(filteredSelectedEndpoints.value))
const knownGroupNames = ref(new Set<string>())
const knownDomainNames = ref(new Set<string>())

watch(allGroupNames, names => {
  const visible = new Set(names)
  const next = new Set([...collapsedGroups.value].filter(group => visible.has(group)))
  for (const group of names) {
    if (!knownGroupNames.value.has(group)) next.add(group)
  }
  knownGroupNames.value = visible
  collapsedGroups.value = next
}, { immediate: true })

watch(allDomains, value => {
  const names = value.map(domain => domain.name)
  const visible = new Set(names)
  const next = new Set([...collapsedDomains.value].filter(domain => visible.has(domain)))
  if (useDomainHierarchy.value) {
    for (const domain of names) {
      if (!knownDomainNames.value.has(domain)) next.add(domain)
    }
  }
  knownDomainNames.value = visible
  collapsedDomains.value = next
}, { immediate: true })

function matchesEndpoint(endpoint: ApiEndpoint, needle: string): boolean {
  return [
    endpoint.summary,
    endpoint.path,
    endpoint.method,
    endpointGroupName(endpoint),
    ...endpoint.tags,
  ].join(' ').toLocaleLowerCase().includes(needle)
}

function toggle(endpointId: string, checked: boolean): void {
  const next = new Set(selected.value)
  if (checked) next.add(endpointId); else next.delete(endpointId)
  updateSelection(next)
}

function updateSelection(next: Set<string>): void {
  selected.value = next
  emit('selection-change', props.endpoints.filter(endpoint => next.has(endpoint.id)).map(endpoint => endpoint.id))
}

function selectedCount(items: ApiEndpoint[]): number {
  return items.filter(item => selected.value.has(item.id)).length
}

function groupChecked(items: ApiEndpoint[]): boolean {
  return items.length > 0 && selectedCount(items) === items.length
}

function groupIndeterminate(items: ApiEndpoint[]): boolean {
  const count = selectedCount(items)
  return count > 0 && count < items.length
}

function toggleGroup(items: ApiEndpoint[], checked: boolean): void {
  const next = new Set(selected.value)
  for (const endpoint of items) {
    if (checked) next.add(endpoint.id); else next.delete(endpoint.id)
  }
  updateSelection(next)
}

function toggleCollapsed(group: string): void {
  const next = new Set(collapsedGroups.value)
  if (next.has(group)) next.delete(group); else next.add(group)
  collapsedGroups.value = next
}

function toggleDomainCollapsed(domain: string): void {
  const next = new Set(collapsedDomains.value)
  if (next.has(domain)) next.delete(domain); else next.add(domain)
  collapsedDomains.value = next
}

function isDomainCollapsed(domain: string): boolean {
  return useDomainHierarchy.value && !query.value.trim() && collapsedDomains.value.has(domain)
}

function isGroupCollapsed(group: string): boolean {
  return !query.value.trim() && collapsedGroups.value.has(group)
}

function groupByDomain(entries: Array<[string, ApiEndpoint[]]>): EndpointDomain[] {
  const grouped = new Map<string, EndpointDomain>()
  for (const entry of entries) {
    const name = domainName(entry[0])
    const domain = grouped.get(name) || { name, groups: [], items: [] }
    domain.groups.push(entry)
    domain.items.push(...entry[1])
    grouped.set(name, domain)
  }
  const order = ['家用', '共享', '本地', '地铁', '其他', '未分类']
  return [...grouped.values()].sort((left, right) => {
    const leftIndex = order.indexOf(left.name)
    const rightIndex = order.indexOf(right.name)
    if (leftIndex >= 0 || rightIndex >= 0) {
      if (leftIndex < 0) return 1
      if (rightIndex < 0) return -1
      return leftIndex - rightIndex
    }
    return left.name.localeCompare(right.name, 'zh-Hans-CN')
  })
}

function domainName(group: string): string {
  if (group === '未分组接口') return '未分类'
  const parts = group.split(' / ').map(part => part.trim()).filter(Boolean)
  const first = parts[0] || ''
  if (first.includes('家用')) return '家用'
  if (first.includes('共享')) return '共享'
  if (first.includes('本地')) return '本地'
  if (first.includes('地铁')) return '地铁'
  if (parts.length === 1) return '其他'
  return first.replace(/业务$/, '') || '其他'
}

function subgroupName(group: string, domain: string): string {
  const parts = group.split(' / ').map(part => part.trim()).filter(Boolean)
  if (parts.length > 1 && domainName(group) === domain) return parts.slice(1).join(' / ')
  return group
}

function highlightText(value: string): HighlightSegment[] {
  const text = value || ''
  const needle = query.value.trim()
  if (!needle) return [{ text, match: false }]
  const lowerText = text.toLocaleLowerCase()
  const lowerNeedle = needle.toLocaleLowerCase()
  const segments: HighlightSegment[] = []
  let cursor = 0
  let index = lowerText.indexOf(lowerNeedle)
  while (index >= 0) {
    if (index > cursor) segments.push({ text: text.slice(cursor, index), match: false })
    const end = index + needle.length
    segments.push({ text: text.slice(index, end), match: true })
    cursor = end
    index = lowerText.indexOf(lowerNeedle, cursor)
  }
  if (cursor < text.length) segments.push({ text: text.slice(cursor), match: false })
  return segments.length ? segments : [{ text, match: false }]
}

function clearSelected(): void {
  updateSelection(new Set())
}

function showAllSearchMatches(): void {
  activeTab.value = 'all'
}
</script>

<template>
  <section class="endpoint-tree" aria-label="接口范围">
    <header class="panel-header endpoint-tree-header"><h2>接口范围</h2><span>{{ selected.size }} 已选</span></header>
    <div class="endpoint-tabs" aria-label="接口范围视图">
      <button data-testid="all-tab" type="button" :class="{ active: activeTab === 'all' }" @click="selectTab('all')">全部接口</button>
      <button data-testid="selected-tab" type="button" :class="{ active: activeTab === 'selected' }" @click="selectTab('selected')">已选接口 <span>{{ selected.size }}</span></button>
    </div>
    <div class="endpoint-search-bar">
      <label class="search-box"><Search :size="15" /><span class="sr-only">搜索接口</span><input :value="query" data-testid="endpoint-search" placeholder="搜索名称或路径" @compositionstart="handleSearchCompositionStart" @compositionend="handleSearchCompositionEnd" @input="handleSearchInput" /></label>
    </div>
    <p v-if="state === 'loading'" class="state-message">正在读取接口...</p>
    <p v-else-if="state === 'failed'" class="state-message state-error">{{ error || '接口读取失败' }}</p>
    <p v-else-if="state === 'empty'" class="state-message">尚无已保存接口，请先导入接口来源。</p>
    <p v-if="state === 'partial'" class="partial-notice">部分接口未能读取，已展示可用结果。</p>
    <p v-if="additionalSearchMatches" class="selected-search-more" data-testid="selected-search-more">
      当前任务已选接口之外还有 {{ additionalSearchMatches }} 个匹配结果。
      <button type="button" class="text-command" @click="showAllSearchMatches">查看全部匹配</button>
    </p>
    <template v-if="activeTab === 'all'">
      <section v-for="domain in domains" :key="domain.name" class="endpoint-domain">
        <div v-if="useDomainHierarchy" class="endpoint-domain-head" role="heading" aria-level="3">
          <label class="domain-select">
            <input
              :data-testid="`domain-select-${domain.name}`"
              type="checkbox"
              :checked="groupChecked(domain.items)"
              :indeterminate.prop="groupIndeterminate(domain.items)"
              @change="toggleGroup(domain.items, ($event.target as HTMLInputElement).checked)"
            />
            <button :data-testid="`domain-toggle-${domain.name}`" type="button" @click="toggleDomainCollapsed(domain.name)">
              <ChevronRight v-if="isDomainCollapsed(domain.name)" :size="15" />
              <ChevronDown v-else :size="14" />
              <span>{{ domain.name }}</span>
            </button>
          </label>
          <span :data-testid="`domain-count-${domain.name}`">{{ selectedCount(domain.items) ? `${selectedCount(domain.items)} 已选 · ` : '' }}{{ domain.groups.length }} 个分组 · {{ domain.items.length }} 个接口</span>
        </div>
        <template v-if="!isDomainCollapsed(domain.name)">
          <div v-for="[group, items] in domain.groups" :key="group" :class="['endpoint-group', { 'endpoint-subgroup': useDomainHierarchy }]">
            <h3 class="endpoint-group-head">
              <label class="group-select">
                <input
                  :data-testid="`group-select-${group}`"
                  type="checkbox"
                  :checked="groupChecked(items)"
                  :indeterminate.prop="groupIndeterminate(items)"
                  @change="toggleGroup(items, ($event.target as HTMLInputElement).checked)"
                />
                <button :data-testid="`group-toggle-${group}`" type="button" @click="toggleCollapsed(group)">
                  <ChevronRight v-if="isGroupCollapsed(group)" :size="14" />
                  <ChevronDown v-else :size="14" />
                  <span :title="group">
                    <template v-for="(segment, index) in highlightText(useDomainHierarchy ? subgroupName(group, domain.name) : group)" :key="`${group}-group-${index}`">
                      <mark v-if="segment.match" class="search-highlight">{{ segment.text }}</mark>
                      <template v-else>{{ segment.text }}</template>
                    </template>
                  </span>
                </button>
              </label>
              <span :data-testid="`group-selected-count-${group}`">{{ selectedCount(items) ? `${selectedCount(items)} 已选 / ` : '' }}{{ items.length }}</span>
            </h3>
            <template v-if="!isGroupCollapsed(group)">
              <label v-for="endpoint in items" :key="endpoint.id" class="endpoint-row" @dblclick="emit('activate', endpoint)">
                <input
                  :data-testid="`endpoint-${endpoint.id}`"
                  type="checkbox"
                  :checked="selected.has(endpoint.id)"
                  @change="toggle(endpoint.id, ($event.target as HTMLInputElement).checked)"
                />
                <button type="button" class="endpoint-open" @click="emit('activate', endpoint)">
                  <span :class="['method-badge', `method-${endpoint.method.toLowerCase()}`]">{{ endpoint.method }}</span>
                  <span class="endpoint-copy">
                    <strong :title="endpoint.summary || endpoint.path">
                      <template v-for="(segment, index) in highlightText(endpoint.summary || endpoint.path)" :key="`${endpoint.id}-summary-${index}`">
                        <mark v-if="segment.match" class="search-highlight">{{ segment.text }}</mark>
                        <template v-else>{{ segment.text }}</template>
                      </template>
                    </strong>
                    <small :title="endpoint.path">
                      <template v-for="(segment, index) in highlightText(endpoint.path)" :key="`${endpoint.id}-path-${index}`">
                        <mark v-if="segment.match" class="search-highlight">{{ segment.text }}</mark>
                        <template v-else>{{ segment.text }}</template>
                      </template>
                    </small>
                  </span>
                </button>
              </label>
            </template>
          </div>
        </template>
      </section>
      <p v-if="state === 'ready' && !filtered.length" class="state-message">没有匹配的接口。</p>
    </template>
    <template v-else>
      <div class="selected-toolbar">
        <span>当前已选 {{ selected.size }} 个接口</span>
        <button type="button" class="text-command" :disabled="!selected.size" @click="clearSelected">清空已选</button>
      </div>
      <div v-for="[group, items] in selectedGroups" :key="group" class="endpoint-group selected-endpoint-group">
        <h3 class="selected-group-head"><span class="selected-group-name" :title="group">{{ group }}</span><span>{{ items.length }}</span></h3>
        <div v-for="endpoint in items" :key="endpoint.id" :data-testid="`selected-endpoint-row-${endpoint.id}`" class="endpoint-row selected-endpoint-row">
          <button type="button" class="endpoint-open selected-endpoint-open" @click="emit('activate', endpoint)">
            <span :class="['method-badge', `method-${endpoint.method.toLowerCase()}`]">{{ endpoint.method }}</span>
            <span :data-testid="`selected-endpoint-summary-${endpoint.id}`" class="endpoint-copy selected-endpoint-copy">
              <strong :title="endpoint.summary || endpoint.path">{{ endpoint.summary || endpoint.path }}</strong>
              <small :title="endpoint.path">{{ endpoint.path }}</small>
            </span>
          </button>
          <button :data-testid="`remove-selected-${endpoint.id}`" class="mini-icon selected-remove-button" type="button" :title="`移除 ${endpoint.summary || endpoint.path}`" @click="toggle(endpoint.id, false)">
            <X :size="14" />
            <span class="sr-only">移除</span>
          </button>
        </div>
      </div>
      <p v-if="!selectedEndpoints.length" class="state-message">还没有选择接口。切回全部接口后，可按分组勾选。</p>
      <p v-else-if="!filteredSelectedEndpoints.length" class="state-message">已选接口里没有匹配结果。</p>
    </template>
  </section>
</template>
