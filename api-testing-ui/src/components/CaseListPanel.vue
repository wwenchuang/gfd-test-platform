<script setup lang="ts">
import { computed, nextTick, ref, watch } from 'vue'
import { ChevronDown, ChevronRight, FileCheck2, Play, Plus, Save, Search, Trash2, X } from 'lucide-vue-next'

import type { ApiEndpoint, CaseVersion, GeneratedCasePreview } from '../api/contracts'
import { compareGroupNames, endpointGroupName } from '../utils/endpointGroups'

const props = withDefaults(defineProps<{
  endpoints: ApiEndpoint[]
  versions: CaseVersion[]
  generatedPreviews?: GeneratedCasePreview[]
  activeVersionId?: string
  activePreviewId?: string
  selectedEndpointIds?: string[]
  saving?: boolean
  running?: boolean
}>(), {
  generatedPreviews: () => [],
  activeVersionId: '',
  activePreviewId: '',
  selectedEndpointIds: () => [],
  saving: false,
  running: false,
})

const emit = defineEmits<{
  'edit-version': [version: CaseVersion]
  'run-version': [version: CaseVersion]
  'delete-version': [version: CaseVersion]
  'toggle-scope': [endpointId: string]
  'edit-preview': [preview: GeneratedCasePreview]
  'save-preview': [preview: GeneratedCasePreview]
  'discard-preview': [previewId: string]
  'save-all-previews': []
}>()

type CaseListItem =
  | { kind: 'version'; id: string; endpoint: ApiEndpoint; name: string; meta: string; version: CaseVersion }
  | { kind: 'preview'; id: string; endpoint: ApiEndpoint; name: string; meta: string; preview: GeneratedCasePreview }

const DEFAULT_COLLAPSE_THRESHOLD = 4
const query = ref('')
const collapsedGroups = ref<Set<string>>(new Set())
const groupElements = new Map<string, HTMLElement>()
const initializedGroupSignature = ref('')
const endpointById = computed(() => new Map(props.endpoints.map(endpoint => [endpoint.id, endpoint])))
const selectedSet = computed(() => new Set(props.selectedEndpointIds))
const totalCount = computed(() => props.versions.length + props.generatedPreviews.length)
const keyword = computed(() => query.value.trim().toLocaleLowerCase())
const allItems = computed<CaseListItem[]>(() => {
  const versions = props.versions.map((version): CaseListItem => {
    const endpoint = endpointById.value.get(version.endpoint_id) || fallbackEndpoint(version.endpoint_id, version.request.method, version.request.path, version.name)
    return {
      kind: 'version',
      id: version.id,
      endpoint,
      name: version.name,
      meta: `v${version.version} · ${originLabel(version.origin)}`,
      version,
    }
  })
  const previews = props.generatedPreviews.map((preview): CaseListItem => {
    const endpoint = endpointById.value.get(preview.endpoint_id) || fallbackEndpoint(preview.endpoint_id, preview.case.request.method, preview.case.request.path, preview.case.name)
    return {
      kind: 'preview',
      id: preview.id,
      endpoint,
      name: preview.case.name,
      meta: `候选 · ${originLabel(preview.origin)}`,
      preview,
    }
  })
  return [...previews, ...versions]
})
const allGroupedItems = computed(() => groupCaseItems(''))
const groupedItems = computed(() => keyword.value ? groupCaseItems(keyword.value) : allGroupedItems.value)
const allGroupNames = computed(() => allGroupedItems.value.map(([group]) => group))
const groupNames = computed(() => groupedItems.value.map(([group]) => group))
const hasGroups = computed(() => groupNames.value.length > 0)
const allGroupsCollapsed = computed(() => hasGroups.value && groupNames.value.every(group => collapsedGroups.value.has(group)))
const hasCollapsedGroups = computed(() => groupNames.value.some(group => collapsedGroups.value.has(group)))

watch(allGroupNames, names => {
  const signature = names.join('\u001f')
  if (signature === initializedGroupSignature.value) return
  initializedGroupSignature.value = signature
  collapsedGroups.value = names.length >= DEFAULT_COLLAPSE_THRESHOLD
    ? new Set(names.slice(1))
    : new Set([...collapsedGroups.value].filter(group => names.includes(group)))
}, { immediate: true })

function groupCaseItems(filterKeyword: string): Array<[string, CaseListItem[]]> {
  const grouped = new Map<string, CaseListItem[]>()
  for (const item of allItems.value) {
    const group = endpointGroupName(item.endpoint)
    if (filterKeyword && !matchesItem(item, group, filterKeyword)) continue
    grouped.set(group, [...(grouped.get(group) || []), item])
  }
  return [...grouped.entries()].sort(([left], [right]) => compareGroupNames(left, right))
}

function matchesItem(item: CaseListItem, group: string, keyword: string): boolean {
  return [
    group,
    item.name,
    item.endpoint.method,
    item.endpoint.path,
    item.meta,
  ].join(' ').toLocaleLowerCase().includes(keyword)
}

function fallbackEndpoint(endpointId: string, method: string, path: string, summary: string): ApiEndpoint {
  return { id: endpointId, method, path, summary, tags: [] }
}

function originLabel(origin: string): string {
  if (origin === 'ai') return 'AI'
  if (origin === 'imported') return '平台'
  return '手工'
}

function isActive(item: CaseListItem): boolean {
  return item.kind === 'preview'
    ? item.id === props.activePreviewId
    : item.id === props.activeVersionId
}

function inScope(endpointId: string): boolean {
  return selectedSet.value.has(endpointId)
}

function scopeTitle(endpointId: string): string {
  return inScope(endpointId) ? '从当前任务范围移除' : '加入当前任务范围'
}

function isGroupCollapsed(group: string): boolean {
  return !keyword.value && collapsedGroups.value.has(group)
}

function toggleGroup(group: string): void {
  const next = new Set(collapsedGroups.value)
  if (next.has(group)) next.delete(group)
  else next.add(group)
  collapsedGroups.value = next
}

function setAllGroups(collapsed: boolean): void {
  collapsedGroups.value = collapsed ? new Set(groupNames.value) : new Set()
}

function setGroupElement(group: string, element: unknown): void {
  if (element instanceof HTMLElement) groupElements.set(group, element)
  else groupElements.delete(group)
}

async function revealGroup(group: string): Promise<void> {
  const next = new Set(collapsedGroups.value)
  next.delete(group)
  collapsedGroups.value = next
  await nextTick()
  groupElements.get(group)?.scrollIntoView?.({ block: 'nearest' })
}
</script>

<template>
  <aside class="case-list-panel" aria-label="用例列表">
    <header class="panel-header">
      <h2>用例列表</h2>
      <span>{{ totalCount }}</span>
    </header>
    <div class="case-list-tools">
      <label class="search-box case-list-search">
        <Search :size="15" />
        <span class="sr-only">搜索用例</span>
        <input v-model="query" data-testid="case-list-search" placeholder="搜索用例、接口或路径" />
      </label>
      <div class="case-list-group-actions" aria-label="用例分组操作">
        <button data-testid="case-list-expand-all" class="text-command" type="button" :disabled="!hasCollapsedGroups" @click="setAllGroups(false)">展开全部</button>
        <button data-testid="case-list-collapse-all" class="text-command" type="button" :disabled="!hasGroups || allGroupsCollapsed" @click="setAllGroups(true)">收起全部</button>
      </div>
      <div v-if="groupedItems.length" class="case-group-index" aria-label="用例分组目录">
        <button
          v-for="[group, items] in groupedItems"
          :key="group"
          :data-testid="`case-list-group-jump-${group}`"
          type="button"
          :class="{ active: !isGroupCollapsed(group) }"
          @click="revealGroup(group)"
        >
          <span :title="group">{{ group }}</span>
          <b>{{ items.length }}</b>
        </button>
      </div>
      <button
        v-if="generatedPreviews.length"
        data-testid="case-preview-save-all"
        class="secondary-command wide"
        type="button"
        :disabled="saving"
        @click="emit('save-all-previews')"
      >
        <Save :size="15" />保存全部候选
      </button>
    </div>
    <div class="case-list-scroll">
      <template v-for="[group, items] in groupedItems" :key="group">
        <section :ref="element => setGroupElement(group, element)" class="case-list-group" :class="{ collapsed: isGroupCollapsed(group) }">
          <h3 :data-testid="`case-list-group-${group}`">
            <button
              :data-testid="`case-list-group-toggle-${group}`"
              class="case-list-group-toggle"
              type="button"
              :aria-expanded="isGroupCollapsed(group) ? 'false' : 'true'"
              @click="toggleGroup(group)"
            >
              <ChevronRight v-if="isGroupCollapsed(group)" :size="15" />
              <ChevronDown v-else :size="15" />
              <span :title="group">{{ group }}</span>
              <b>{{ items.length }}</b>
            </button>
          </h3>
          <template v-if="!isGroupCollapsed(group)">
            <article
              v-for="item in items"
              :key="`${item.kind}-${item.id}`"
              :data-testid="item.kind === 'preview' ? `case-preview-${item.id}` : `case-version-${item.id}`"
              class="case-list-row"
              :class="{ active: isActive(item), preview: item.kind === 'preview' }"
            >
              <button
                type="button"
                class="case-list-main"
                :data-testid="item.kind === 'preview' ? `case-preview-edit-${item.id}` : `case-version-edit-${item.id}`"
                @click="item.kind === 'preview' ? emit('edit-preview', item.preview) : emit('edit-version', item.version)"
              >
                <span :class="['method-badge', `method-${item.endpoint.method.toLowerCase()}`]">{{ item.endpoint.method }}</span>
                <span class="case-list-copy">
                  <strong :title="item.name">{{ item.name }}</strong>
                  <small :title="item.endpoint.path">{{ item.endpoint.path }}</small>
                </span>
              </button>
              <em>{{ item.meta }}</em>
              <span class="case-list-actions">
                <button
                  v-if="item.kind === 'preview'"
                  :data-testid="`case-preview-save-${item.id}`"
                  class="mini-icon"
                  type="button"
                  title="保存候选用例"
                  :disabled="saving"
                  @click="emit('save-preview', item.preview)"
                >
                  <Save :size="14" />
                </button>
                <button
                  v-if="item.kind === 'preview'"
                  :data-testid="`case-preview-discard-${item.id}`"
                  class="mini-icon danger"
                  type="button"
                  title="丢弃候选用例"
                  :disabled="saving"
                  @click="emit('discard-preview', item.id)"
                >
                  <X :size="14" />
                </button>
                <button
                  v-if="item.kind === 'version'"
                  :data-testid="`case-version-run-${item.id}`"
                  class="mini-icon"
                  type="button"
                  title="执行该用例"
                  :disabled="running"
                  @click="emit('run-version', item.version)"
                >
                  <Play :size="14" />
                </button>
                <button
                  v-if="item.kind === 'version'"
                  :data-testid="`case-version-scope-${item.id}`"
                  class="mini-icon"
                  type="button"
                  :title="scopeTitle(item.endpoint.id)"
                  @click="emit('toggle-scope', item.endpoint.id)"
                >
                  <X v-if="inScope(item.endpoint.id)" :size="14" />
                  <Plus v-else :size="14" />
                </button>
                <button
                  v-if="item.kind === 'version'"
                  :data-testid="`case-version-delete-${item.id}`"
                  class="mini-icon danger"
                  type="button"
                  title="删除该用例"
                  :disabled="saving || running"
                  @click="emit('delete-version', item.version)"
                >
                  <Trash2 :size="14" />
                </button>
              </span>
            </article>
          </template>
        </section>
      </template>
      <p v-if="!groupedItems.length" class="section-empty">
        <FileCheck2 :size="16" />
        {{ query.trim() ? '没有匹配的用例。' : '暂无用例，选择接口后可手工编辑或生成候选。' }}
      </p>
    </div>
  </aside>
</template>
