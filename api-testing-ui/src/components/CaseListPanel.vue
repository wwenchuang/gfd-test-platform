<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { FileCheck2, FolderInput, History, Play, Plus, Save, Search, ShieldCheck, Trash2, X } from 'lucide-vue-next'

import type { ApiEndpoint, CaseVersion, GeneratedCasePreview } from '../api/contracts'
import {
  buildCaseGroupTree,
  caseBrowseGroupName,
  caseGroupAncestorIds,
  caseGroupNodeIds,
  caseSearchText,
  isOneTimeCase,
  matchesCaseWorkView,
  type CaseGroupNode,
  type CaseListItem,
  type CaseWorkView,
} from '../utils/caseListPresentation'
import { compareGroupNames, endpointGroupName } from '../utils/endpointGroups'
import { preferredBusinessLineId } from '../utils/businessLines'
import { applicationBusinessLabel } from '../utils/testApplications'
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
  'open-endpoints': []
  'open-debug-history': [version: CaseVersion]
  'open-baseline': [version: CaseVersion]
}>()

const WORK_VIEWS: Array<{ id: CaseWorkView; label: string }> = [
  { id: 'all', label: '全部' },
  { id: 'regular', label: '待调试' },
  { id: 'debugged', label: '已调试' },
  { id: 'baseline', label: '已基线' },
  { id: 'task', label: '当前任务' },
  { id: 'orchestrated', label: '有编排' },
  { id: 'one-time', label: '一次性' },
  { id: 'candidate', label: '未保存候选' },
]
const viewGuidance: Record<CaseWorkView, string> = {
  all: '按用例版本计数，同一接口可有多条用例。各视图可能重叠，数量不能相加。',
  regular: '已保存、尚无调试记录且未采纳基线。下一步：打开用例，核对参数和断言后“保存并调试”。',
  debugged: '有调试记录，包括通过、失败和异常；是否通过以每条用例的调试结果为准。',
  baseline: '已采纳的基线版本；不代表本轮回归通过。执行任务后到“测试报告”查看结果。',
  task: '当前任务范围内的用例，未采纳为基线的草稿不会随任务回归执行。',
  orchestrated: '包含前后处理或前置、清理步骤。写操作需确认清理成功，再采纳基线。',
  'one-time': '一次性人工用例可保留在库中；普通批量回归默认跳过，定时任务需显式开启。',
  candidate: '生成后尚未保存的候选。先检查内容并保存，再调试；未保存候选可能在切换范围后清除。',
}

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
    const displayEndpointId = version.current_endpoint_id || version.endpoint_id
    const endpoint = endpointById.value.get(displayEndpointId)
      || fallbackEndpoint(version.endpoint_id, version.request.method, version.request.path, version.name)
    const groupName = version.group_name?.trim() || endpointGroupName(endpoint)
    const scope = caseScopeLabel(version, endpoint)
    return {
      kind: 'version', id: version.id, endpoint, name: version.name,
      meta: `v${version.version} · ${originLabel(version.origin)} · ${version.source_state === 'needs_adaptation' ? '待适配 · ' : ''}${scope}`,
      groupName,
      browseGroupName: caseBrowseGroupName(scope, groupName),
      version,
    }
  })
  const previews = props.generatedPreviews.map((preview): CaseListItem => {
    const endpoint = endpointById.value.get(preview.endpoint_id)
      || fallbackEndpoint(preview.endpoint_id, preview.case.request.method, preview.case.request.path, preview.case.name)
    const groupName = endpointGroupName(endpoint)
    const scope = caseScopeLabel(preview.case, endpoint)
    return {
      kind: 'preview', id: preview.id, endpoint, name: preview.case.name,
      meta: `候选 · ${originLabel(preview.origin)} · ${scope}`,
      groupName,
      browseGroupName: caseBrowseGroupName(scope, groupName),
      preview,
    }
  })
  return [...previews, ...versions]
})
const workViewCounts = computed(() => Object.fromEntries(WORK_VIEWS.map(view => [
  view.id,
  allItems.value.filter(item => (
    (!keyword.value || caseSearchText(item).toLocaleLowerCase().includes(keyword.value))
    && matchesCaseWorkView(item, view.id, selectedEndpointSet.value)
  )).length,
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
  : `${visibleItems.value.length} 条 · ${visibleTree.value.length} 个应用/业务范围`)
const selectedCount = computed(() => selectedVersionIds.value.size)

watch(() => allItems.value.map(item => `${item.kind}:${item.id}:${item.browseGroupName || item.groupName}`).join('\u001f'), signature => {
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
  caseGroupAncestorIds(active.browseGroupName || active.groupName).forEach(id => next.add(id))
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

function caseScopeLabel(item: Pick<CaseVersion, 'app_package' | 'app_name' | 'business'>, endpoint: ApiEndpoint): string {
  const business = item.business || preferredBusinessLineId([
    endpointGroupName(endpoint), endpoint.summary, endpoint.path,
  ], item.app_package)
  return applicationBusinessLabel(item.app_package, item.app_name, business)
}

function baselinePolicyLabel(policy?: string): string {
  if (policy === 'direct') return '可直接进入基线校验'
  if (policy === 'guarded') return '需完成前置和清理'
  if (policy === 'excluded') return '默认排除定时基线'
  return '需人工补全编排'
}

function debugStatusLabel(status?: string): string {
  if (status === 'PASSED') return '调试通过'
  if (status === 'FAILED') return '调试失败'
  if (status === 'BROKEN') return '调试异常'
  if (status === 'CANCELLED') return '调试取消'
  return status ? '调试未完成' : ''
}

function regressionStatusLabel(status?: string): string {
  if (status === 'PASSED') return '回归通过'
  if (status === 'FAILED') return '回归失败'
  if (status === 'BROKEN') return '回归异常'
  return status ? '回归未完成' : ''
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
    <header class="panel-header"><h2>用例列表</h2><span>{{ totalCount }} 条</span></header>
    <div class="case-list-tools">
      <p class="case-count-note">已保存 {{ versions.length }} 条 · 未保存候选 {{ generatedPreviews.length }} 条</p>
      <div class="case-work-views" role="tablist" aria-label="用例工作视图">
        <button v-for="view in WORK_VIEWS" :key="view.id" :data-testid="`case-work-view-${view.id}`" type="button" role="tab" :aria-selected="workView === view.id" :class="{ active: workView === view.id }" @click="workView = view.id">
          <span>{{ view.label }}</span><b>{{ workViewCounts[view.id] }}</b>
        </button>
      </div>
      <p class="case-view-guidance" data-testid="case-view-guidance" role="status">{{ viewGuidance[workView] }}</p>
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
                <span v-if="item.kind === 'version'" class="case-lifecycle-badges">
                  <i v-if="item.version.source_state === 'needs_adaptation'" class="lifecycle-adapt">待适配</i>
                  <i v-if="item.version.lifecycle?.debug_status" :class="item.version.lifecycle.debug_status === 'PASSED' ? 'lifecycle-pass' : 'lifecycle-fail'">{{ debugStatusLabel(item.version.lifecycle.debug_status) }}</i>
                  <i v-if="item.version.lifecycle?.baseline_status === 'active'" class="lifecycle-baseline">已基线</i>
                  <i v-if="item.version.lifecycle?.regression_status" :class="item.version.lifecycle.regression_status === 'PASSED' ? 'lifecycle-pass' : 'lifecycle-fail'">{{ regressionStatusLabel(item.version.lifecycle.regression_status) }}</i>
                  <i v-if="isOneTimeCase(item)" class="lifecycle-one-time">一次性</i>
                  <i v-if="!isOneTimeCase(item) && !item.version.lifecycle?.debug_status && item.version.lifecycle?.baseline_status !== 'active' && item.version.source_state !== 'needs_adaptation'" class="lifecycle-regular">普通用例</i>
                </span>
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
              <button v-if="item.kind === 'version' && item.version.lifecycle?.debug_execution_id" :data-testid="`case-version-debug-history-${item.id}`" class="mini-icon" type="button" title="查看最近调试记录" @click="emit('open-debug-history', item.version)"><History :size="14" /></button>
              <button v-if="item.kind === 'version' && item.version.lifecycle?.baseline_id" :data-testid="`case-version-baseline-${item.id}`" class="mini-icon" type="button" title="管理该用例基线" @click="emit('open-baseline', item.version)"><ShieldCheck :size="14" /></button>
              <button v-if="item.kind === 'version'" :data-testid="`case-version-run-${item.id}`" class="mini-icon" type="button" title="执行该用例" :disabled="running" @click="emit('run-version', item.version)"><Play :size="14" /></button>
              <button v-if="item.kind === 'version'" :data-testid="`case-version-scope-${item.id}`" class="mini-icon" type="button" :title="scopeTitle(item.endpoint.id)" @click="emit('toggle-scope', item.endpoint.id)"><X v-if="inScope(item.endpoint.id)" :size="14" /><Plus v-else :size="14" /></button>
              <button v-if="item.kind === 'version'" :data-testid="`case-version-delete-${item.id}`" class="mini-icon danger" type="button" title="删除该用例" :disabled="saving || running" @click="emit('delete-version', item.version)"><Trash2 :size="14" /></button>
            </span>
          </article>
        </template>
      </CaseGroupBranch>
      <div v-if="!visibleTree.length" data-testid="case-list-empty" class="section-empty case-list-empty-actions">
        <FileCheck2 :size="16" />
        <span>{{ totalCount ? '当前筛选下没有匹配用例，请调整视图或搜索条件。' : '当前接口版本还没有用例。' }}</span>
        <button v-if="!totalCount" data-testid="case-list-open-endpoints" class="primary-command" type="button" @click="emit('open-endpoints')"><Plus :size="15" />从接口创建用例</button>
      </div>
    </div>
  </aside>
</template>
