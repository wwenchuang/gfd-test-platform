<script setup lang="ts">
import { computed, nextTick, ref, watch } from 'vue'
import { ChevronDown, ChevronRight, Search, X } from 'lucide-vue-next'

import type { ApiEndpoint } from '../api/contracts'
import { endpointGroupName, endpointSubgroupName, groupEndpointDomains, groupEndpoints } from '../utils/endpointGroups'

interface HighlightSegment {
  text: string
  match: boolean
}

const props = withDefaults(defineProps<{
  open: boolean
  endpoints: ApiEndpoint[]
  title: string
  allowManual?: boolean
  busy?: boolean
  error?: string
}>(), { allowManual: true, busy: false, error: '' })

const emit = defineEmits<{
  select: [endpoint: ApiEndpoint]
  manual: []
  close: []
}>()

const query = ref('')
const searchInput = ref<HTMLInputElement | null>(null)
const collapsedGroups = ref(new Set<string>())
const collapsedDomains = ref(new Set<string>())
let returnTarget: HTMLElement | null = null

const filteredEndpoints = computed(() => {
  const needle = normalizedQuery()
  if (!needle) return props.endpoints
  return props.endpoints.filter(endpoint => endpointSearchText(endpoint).includes(needle))
})
const groups = computed(() => groupEndpoints(filteredEndpoints.value))
const domains = computed(() => groupEndpointDomains(groups.value))
const allGroups = computed(() => groupEndpoints(props.endpoints))
const useDomainHierarchy = computed(() => allGroups.value.length > 8)

watch(() => props.endpoints, endpoints => {
  const entries = groupEndpoints(endpoints)
  collapsedGroups.value = new Set(entries.map(([name]) => name))
  collapsedDomains.value = useDomainHierarchy.value
    ? new Set(groupEndpointDomains(entries).map(domain => domain.name))
    : new Set()
}, { immediate: true })

watch(() => props.open, async open => {
  if (open) {
    returnTarget = document.activeElement instanceof HTMLElement ? document.activeElement : null
    query.value = ''
    await nextTick()
    searchInput.value?.focus()
  } else {
    returnTarget?.focus()
    returnTarget = null
  }
}, { immediate: true })

function normalizedQuery(): string {
  return query.value.trim().toLocaleLowerCase()
}

function endpointSearchText(endpoint: ApiEndpoint): string {
  return [
    endpoint.summary,
    endpoint.path,
    endpoint.method,
    endpointGroupName(endpoint),
    ...endpoint.tags,
  ].join(' ').toLocaleLowerCase()
}

function isCollapsed(group: string): boolean {
  return !normalizedQuery() && collapsedGroups.value.has(group)
}

function toggleGroup(group: string): void {
  const next = new Set(collapsedGroups.value)
  if (next.has(group)) next.delete(group)
  else next.add(group)
  collapsedGroups.value = next
}

function isDomainCollapsed(domain: string): boolean {
  return useDomainHierarchy.value && !normalizedQuery() && collapsedDomains.value.has(domain)
}

function toggleDomain(domain: string): void {
  const next = new Set(collapsedDomains.value)
  if (next.has(domain)) next.delete(domain)
  else next.add(domain)
  collapsedDomains.value = next
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

function close(): void {
  emit('close')
}

function handleKeydown(event: KeyboardEvent): void {
  if (event.key === 'Escape') close()
}
</script>

<template>
  <div v-if="open" class="endpoint-picker-backdrop" @click.self="close">
    <section class="endpoint-picker" role="dialog" aria-modal="true" :aria-label="title" @keydown="handleKeydown">
      <header>
        <h3>{{ title }}</h3>
        <button class="mini-icon" type="button" title="关闭接口选择" @click="close"><X :size="16" /><span class="sr-only">关闭</span></button>
      </header>
      <label class="search-box endpoint-picker-search">
        <Search :size="16" />
        <span class="sr-only">搜索接口</span>
        <input ref="searchInput" v-model="query" data-testid="endpoint-picker-search" placeholder="搜索名称、方法、路径或分组" />
      </label>
      <div class="endpoint-picker-results">
        <section v-for="domain in domains" :key="domain.name" class="endpoint-picker-domain">
          <button
            v-if="useDomainHierarchy"
            :data-testid="`endpoint-picker-domain-${domain.name}`"
            class="endpoint-picker-domain-toggle"
            type="button"
            :aria-expanded="isDomainCollapsed(domain.name) ? 'false' : 'true'"
            @click="toggleDomain(domain.name)"
          >
            <ChevronRight v-if="isDomainCollapsed(domain.name)" :size="16" />
            <ChevronDown v-else :size="16" />
            <strong>{{ domain.name }}</strong>
            <span>{{ domain.groups.length }} 个分组 · {{ domain.endpoints.length }} 个接口</span>
          </button>
          <template v-if="!isDomainCollapsed(domain.name)">
            <section v-for="[group, endpoints] in domain.groups" :key="group" :class="['endpoint-picker-group', { 'endpoint-picker-subgroup': useDomainHierarchy }]">
              <button
                :data-testid="`endpoint-picker-group-${group}`"
                class="endpoint-picker-group-toggle"
                type="button"
                :aria-expanded="isCollapsed(group) ? 'false' : 'true'"
                @click="toggleGroup(group)"
              >
                <ChevronRight v-if="isCollapsed(group)" :size="15" />
                <ChevronDown v-else :size="15" />
                <span :title="group">
                  <template v-for="(segment, index) in highlightText(useDomainHierarchy ? endpointSubgroupName(group, domain.name) : group)" :key="`${group}-${index}`">
                    <mark v-if="segment.match" class="search-highlight">{{ segment.text }}</mark>
                    <template v-else>{{ segment.text }}</template>
                  </template>
                </span>
                <b>{{ endpoints.length }}</b>
              </button>
              <template v-if="!isCollapsed(group)">
                <button
                  v-for="endpoint in endpoints"
                  :key="endpoint.id"
                  :data-testid="`endpoint-picker-option-${endpoint.id}`"
                  class="endpoint-picker-option"
                  type="button"
                  :disabled="busy"
                  @click="emit('select', endpoint)"
                >
                  <span :class="['method-badge', `method-${endpoint.method.toLowerCase()}`]">{{ endpoint.method }}</span>
                  <span>
                    <strong>
                      <template v-for="(segment, index) in highlightText(endpoint.summary || endpoint.path)" :key="`${endpoint.id}-summary-${index}`">
                        <mark v-if="segment.match" class="search-highlight">{{ segment.text }}</mark>
                        <template v-else>{{ segment.text }}</template>
                      </template>
                    </strong>
                    <code>
                      <template v-for="(segment, index) in highlightText(endpoint.path)" :key="`${endpoint.id}-path-${index}`">
                        <mark v-if="segment.match" class="search-highlight">{{ segment.text }}</mark>
                        <template v-else>{{ segment.text }}</template>
                      </template>
                    </code>
                  </span>
                </button>
              </template>
            </section>
          </template>
        </section>
        <p v-if="!groups.length" class="state-message">没有匹配的接口。</p>
      </div>
      <p v-if="error" data-testid="endpoint-picker-error" class="inline-error">{{ error }}</p>
      <footer v-if="allowManual || busy">
        <span v-if="busy">正在读取完整接口定义…</span>
        <button v-if="allowManual" data-testid="endpoint-picker-manual" class="secondary-command" type="button" :disabled="busy" @click="emit('manual')">手工配置请求</button>
      </footer>
    </section>
  </div>
</template>
