<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { FileCheck2, FolderInput, Play, Plus, Save, Search, Trash2, X } from 'lucide-vue-next'

import type { ApiEndpoint, CaseVersion, GeneratedCasePreview } from '../api/contracts'
import {
  buildCaseGroupTree,
  caseGroupAncestorIds,
  caseGroupNodeIds,
  caseSearchText,
  matchesCaseWorkView,
  type CaseGroupNode,
  type CaseListItem,
  type CaseWorkView,
} from '../utils/caseListPresentation'
import { compareGroupNames, endpointGroupName } from '../utils/endpointGroups'
import CaseGroupBranch from './CaseGroupBranch.vue'
import CaseGroupPicker from './CaseGroupPicker.vue'
import SearchHighlight from './SearchHighlight.vue'

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
  'update-version-group': [version: CaseVersion, groupName: string]
  'update-version-groups': [versionIds: string[], groupName: string]
}>()

const WORK_VIEWS: Array<{ id: CaseWorkView; label: string }> = [
  { id: 'all', label: '全部' },
  { id: 'task', label: '当前任务' },
  { id: 'orchestrated', label: '有编排' },
  { id: 'one-time', label: '一次性' },
  { id: 'candidate', label: '候选' },
]

const query = ref('')
const workView = ref<CaseWorkView>('all')
const expandedNodeIds = ref<Set<string>>(new Set())
const selectedVersionIds = ref<Set<string>>(new Set())
const activeGroupPickerId = ref('')
const batchPickerOpen = ref(false)
const initializedSignature = ref('')

const endpointById = computed(() => new Map(props.endpoints.map(endpoint => [endpoint.id, endpoint])))
const selectedEndpointSet = computed(() => new Set(props.selectedEndpointIds))
const totalCount = computed(() => props.versions.length + props.generatedPreviews.length)
const keyword = computed(() => query.value.trim().toLocaleLowerCase())
const allItems = computed<CaseListItem[]>(() => {
  const versions = props.versions.map((version): CaseListItem => {
    const endpoint = endpointById.value.get(version.endpoint_id)
      || fallbackEndpoint(version.endpoint_id, version.request.method, version.request.path, version.name)
    return {
      kind: 'version', id: version.id, endpoint, name: version.name,
      meta: `v${version.version} · ${originLabel(version.origin)}`,
      groupName: version.group_name?.trim() || endpointGroupName(endpoint), version,
    }
  })
  const previews = props.generatedPreviews.map((preview): CaseListItem => {
    const endpoint = endpointById.value.get(preview.endpoint_id)
      || fallbackEndpoint(preview.endpoint_id, preview.case.request.method, preview.case.request.path, preview.case.name)
    return {
      kind: 'preview', id: preview.id, endpoint, name: preview.case.name,
      meta: `候选 · ${originLabel(preview.origin)}`, groupName: endpointGroupName(endpoint), preview,
    }
  })
  return [...previews, ...versions]
})
const workViewCounts = computed(() => Object.fromEntries(WORK_VIEWS.map(view => [
  view.id,
  allItems.value.filter(item => matchesCaseWorkView(item, view.id, selectedEndpointSet.value)).length,
])))
const viewItems = computed(() => allItems.value.filter(item => matchesCaseWorkView(item, workView.value, selectedEndpointSet.value)))
const visibleItems = computed(() => viewItems.value.filter(item => !keyword.value || caseSearchText(item).toLocaleLowerCase().includes(keyword.value)))
const viewTree = computed(() => buildCaseGroupTree(viewItems.value))
const visibleTree = computed(() => buildCaseGroupTree(visibleItems.value))
const visibleNodeIds = computed(() => caseGroupNodeIds(visibleTree.value))
const expandedIds = computed(() => [...expandedNodeIds.value])
const hasGroups = computed(() => visibleTree.value.length > 0)
const hasCollapsedGroups = computed(() => visibleNodeIds.value.some(id => !expandedNodeIds.value.has(id)))
const allGroupsCollapsed = computed(() => hasGroups.value && visibleNodeIds.value.every(id => !expandedNodeIds.value.has(id)))
const groupOptions = computed(() => [...new Set(allItems.value.map(item => item.groupName))].sort(compareGroupNames))
const groupStateText = computed(() => keyword.value
  ? `搜索命中 ${visibleItems.value.length} 条用例`
  : `${visibleItems.value.length} 条 · ${visibleTree.value.length} 个根目录`)
const selectedCount = computed(() => selectedVersionIds.value.size)

watch(() => allItems.value.map(item => `${item.kind}:${item.id}:${item.groupName}`).join('\u001f'), signature => {
  const validVersionIds = new Set(props.versions.map(version => version.id))
  selectedVersionIds.value = new Set([...selectedVersionIds.value].filter(id => validVersionIds.has(id)))
  if (signature === initializedSignature.value) return
  initializedSignature.value = signature
  expandedNodeIds.value = defaultExpandedNodes(buildCaseGroupTree(allItems.value))
}, { immediate: true })

watch([() => props.activeVersionId, () => props.activePreviewId], () => {
  const active = allItems.value.find(item => item.kind === 'version' ? item.id === props.activeVersionId : item.id === props.activePreviewId)
  if (!active) return
  const next = new Set(expandedNodeIds.value)
  caseGroupAncestorIds(active.groupName).forEach(id => next.add(id))
  expandedNodeIds.value = next
}, { immediate: true })

watch(viewTree, tree => {
  if (!tree.length) return
  const viewIds = caseGroupNodeIds(tree)
  if (viewIds.some(id => expandedNodeIds.value.has(id))) return
  const next = new Set(expandedNodeIds.value)
  addFirstItemPath(tree[0], next)
  expandedNodeIds.value = next
})

function defaultExpandedNodes(tree: CaseGroupNode[]): Set<string> {
  const next = new Set<string>()
  if (tree[0]) addFirstItemPath(tree[0], next)
  return next
}

function addFirstItemPath(node: CaseGroupNode, target: Set<string>): void {
  target.add(node.id)
  if (!node.items.length && node.children[0]) addFirstItemPath(node.children[0], target)
}

function fallbackEndpoint(endpointId: string, method: string, path: string, summary: string): ApiEndpoint {
  return { id: endpointId, method, path, summary, tags: [] }
}

function originLabel(origin: string): string {
  if (origin === 'ai') return 'AI'
  if (origin === 'imported') return '平台'
  return '手工'
}

function baselinePolicyLabel(policy?: string): string {
  if (policy === 'direct') return '可直接进入基线校验'
  if (policy === 'guarded') return '需完成前置和清理'
  if (policy === 'excluded') return '默认排除定时基线'
  return '需人工补全编排'
}

function isActive(item: CaseListItem): boolean {
  return item.kind === 'preview' ? item.id === props.activePreviewId : item.id === props.activeVersionId
}

function inScope(endpointId: string): boolean {
  return selectedEndpointSet.value.has(endpointId)
}

function scopeTitle(endpointId: string): string {
  return inScope(endpointId) ? '从当前任务范围移除' : '加入当前任务范围'
}

function toggleGroup(nodeId: string): void {
  if (keyword.value) return
  const next = new Set(expandedNodeIds.value)
  if (next.has(nodeId)) next.delete(nodeId)
  else next.add(nodeId)
  expandedNodeIds.value = next
}

function setAllGroups(expanded: boolean): void {
  expandedNodeIds.value = expanded ? new Set(visibleNodeIds.value) : new Set()
}

function toggleSelected(versionId: string): void {
  const next = new Set(selectedVersionIds.value)
  if (next.has(versionId)) next.delete(versionId)
  else next.add(versionId)
  selectedVersionIds.value = next
}

function openGroupPicker(versionId: string): void {
  activeGroupPickerId.value = activeGroupPickerId.value === versionId ? '' : versionId
  batchPickerOpen.value = false
}

function chooseSingleGroup(item: CaseListItem, groupName: string): void {
  if (item.kind !== 'version') return
  activeGroupPickerId.value = ''
  if (groupName !== item.groupName) emit('update-version-group', item.version, groupName)
}

function chooseBatchGroup(groupName: string): void {
  batchPickerOpen.value = false
  const ids = props.versions.filter(version => selectedVersionIds.value.has(version.id)).map(version => version.id)
  if (ids.length) emit('update-version-groups', ids, groupName)
}
</script>

<template>
  <aside class="case-list-panel" aria-label="用例列表">
    <header class="panel-header"><h2>用例列表</h2><span>{{ totalCount }}</span></header>
    <div class="case-list-tools">
      <div class="case-work-views" role="tablist" aria-label="用例工作视图">
        <button v-for="view in WORK_VIEWS" :key="view.id" :data-testid="`case-work-view-${view.id}`" type="button" role="tab" :aria-selected="workView === view.id" :class="{ active: workView === view.id }" @click="workView = view.id">
          <span>{{ view.label }}</span><b>{{ workViewCounts[view.id] }}</b>
        </button>
      </div>
      <label class="search-box case-list-search"><Search :size="15" /><span class="sr-only">搜索用例</span><input v-model="query" data-testid="case-list-search" placeholder="搜索用例、接口或路径" /></label>
      <div v-if="visibleTree.length" data-testid="case-list-group-toolbar" class="case-list-group-toolbar" aria-label="用例分组视图">
        <span data-testid="case-list-group-summary" class="case-list-group-summary"><strong>分组浏览</strong><small>{{ groupStateText }}</small></span>
        <span class="case-list-group-actions" aria-label="用例分组操作">
          <button data-testid="case-list-expand-all" class="text-command" type="button" :disabled="!hasCollapsedGroups || Boolean(keyword)" @click="setAllGroups(true)">展开全部</button>
          <button data-testid="case-list-collapse-all" class="text-command" type="button" :disabled="!hasGroups || allGroupsCollapsed || Boolean(keyword)" @click="setAllGroups(false)">收起全部</button>
        </span>
      </div>
      <div v-if="selectedCount" data-testid="case-batch-toolbar" class="case-batch-toolbar">
        <strong>已选 {{ selectedCount }} 条</strong>
        <span class="case-group-picker-anchor">
          <button data-testid="case-batch-move" class="secondary-command" type="button" :disabled="saving || running" @click="batchPickerOpen = !batchPickerOpen; activeGroupPickerId = ''"><FolderInput :size="15" />移动分组</button>
          <CaseGroupPicker v-if="batchPickerOpen" :groups="groupOptions" title="批量移动分组" @select="chooseBatchGroup" @close="batchPickerOpen = false" />
        </span>
        <button data-testid="case-batch-clear" class="text-command" type="button" @click="selectedVersionIds = new Set()">清空选择</button>
      </div>
      <button v-if="generatedPreviews.length" data-testid="case-preview-save-all" class="secondary-command wide" type="button" :disabled="saving" @click="emit('save-all-previews')"><Save :size="15" />保存全部候选</button>
    </div>
    <div class="case-list-scroll">
      <CaseGroupBranch v-for="node in visibleTree" :key="node.id" :node="node" :expanded-ids="expandedIds" :query="query" :force-expanded="Boolean(keyword)" @toggle="toggleGroup">
        <template #item="{ item }">
          <article :data-testid="item.kind === 'preview' ? `case-preview-${item.id}` : `case-version-${item.id}`" class="case-list-row" :class="{ active: isActive(item), preview: item.kind === 'preview', selected: item.kind === 'version' && selectedVersionIds.has(item.id) }">
            <label v-if="item.kind === 'version'" class="case-row-select" :title="`选择用例 ${item.name}`">
              <input type="checkbox" :data-testid="`case-version-select-${item.id}`" :checked="selectedVersionIds.has(item.id)" @change="toggleSelected(item.id)" />
              <span class="sr-only">选择 {{ item.name }}</span>
            </label>
            <span v-else class="case-row-select-placeholder" aria-hidden="true" />
            <button type="button" class="case-list-main" :data-testid="item.kind === 'preview' ? `case-preview-edit-${item.id}` : `case-version-edit-${item.id}`" @click="item.kind === 'preview' ? emit('edit-preview', item.preview) : emit('edit-version', item.version)">
              <span :class="['method-badge', `method-${item.endpoint.method.toLowerCase()}`]">{{ item.endpoint.method }}</span>
              <span class="case-list-copy">
                <strong :title="item.name"><SearchHighlight :text="item.name" :query="query" /></strong>
                <small :title="item.endpoint.path"><SearchHighlight :text="item.endpoint.path" :query="query" /></small>
                <span v-if="item.kind === 'preview' && item.preview.workflow" class="workflow-preview-line" :title="item.preview.workflow.reason"><b>{{ item.preview.workflow.label }}</b><i>{{ baselinePolicyLabel(item.preview.workflow.baseline_policy) }}</i></span>
              </span>
            </button>
            <em>{{ item.meta }}</em>
            <span class="case-list-actions">
              <span v-if="item.kind === 'version'" class="case-group-picker-anchor">
                <button :data-testid="`case-version-group-${item.id}`" class="mini-icon" type="button" title="移动分组" :disabled="saving || running" @click="openGroupPicker(item.id)"><FolderInput :size="14" /></button>
                <CaseGroupPicker v-if="activeGroupPickerId === item.id" :groups="groupOptions" :current-group="item.groupName" @select="chooseSingleGroup(item, $event)" @close="activeGroupPickerId = ''" />
              </span>
              <button v-if="item.kind === 'preview'" :data-testid="`case-preview-save-${item.id}`" class="mini-icon" type="button" title="保存候选用例" :disabled="saving" @click="emit('save-preview', item.preview)"><Save :size="14" /></button>
              <button v-if="item.kind === 'preview'" :data-testid="`case-preview-discard-${item.id}`" class="mini-icon danger" type="button" title="丢弃候选用例" :disabled="saving" @click="emit('discard-preview', item.id)"><X :size="14" /></button>
              <button v-if="item.kind === 'version'" :data-testid="`case-version-run-${item.id}`" class="mini-icon" type="button" title="执行该用例" :disabled="running" @click="emit('run-version', item.version)"><Play :size="14" /></button>
              <button v-if="item.kind === 'version'" :data-testid="`case-version-scope-${item.id}`" class="mini-icon" type="button" :title="scopeTitle(item.endpoint.id)" @click="emit('toggle-scope', item.endpoint.id)"><X v-if="inScope(item.endpoint.id)" :size="14" /><Plus v-else :size="14" /></button>
              <button v-if="item.kind === 'version'" :data-testid="`case-version-delete-${item.id}`" class="mini-icon danger" type="button" title="删除该用例" :disabled="saving || running" @click="emit('delete-version', item.version)"><Trash2 :size="14" /></button>
            </span>
          </article>
        </template>
      </CaseGroupBranch>
      <p v-if="!visibleTree.length" data-testid="case-list-empty" class="section-empty"><FileCheck2 :size="16" />{{ totalCount ? '当前筛选下没有匹配用例，请调整视图或搜索条件。' : '暂无用例，选择接口后可手工编辑或生成候选。' }}</p>
    </div>
  </aside>
</template>
