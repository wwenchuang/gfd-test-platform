<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ArrowRight, Bell, Check, KeyRound, Pencil, Plus, RotateCcw, Save, Trash2 } from 'lucide-vue-next'

import type { EnvironmentAsset, EnvironmentRevisionSummary, EnvironmentView, SourceRevision } from '../api/contracts'
import { apiClient } from '../api/client'
import EnvironmentAssetList from '../components/EnvironmentAssetList.vue'
import { useContextStore } from '../stores/context'
import { useNotificationsStore } from '../stores/notifications'
import { type EnvironmentPayload, useSetupStore } from '../stores/setup'

type Pair = { key: string; value: string }
type ServiceRow = { key: string; name: string; module: string; base_url: string }
type DetailTab = 'overview' | 'services' | 'variables' | 'history'
const detailTabs: Array<{ id: DetailTab; label: string }> = [
  { id: 'overview', label: '概览' },
  { id: 'services', label: '服务地址' },
  { id: 'variables', label: '变量与凭证' },
  { id: 'history', label: '版本历史' },
]

const route = useRoute()
const router = useRouter()
const context = useContextStore()
const setup = useSetupStore()
const notifications = useNotificationsStore()

const projectId = ref('')
const environmentStatus = ref<'active' | 'archived'>('active')
const selectedEnvironmentId = ref('')
const sourceRevisionId = ref('')
const environmentId = ref<string | null>(null)
const editing = ref(false)
const creating = ref(false)
const loadingAssets = ref(false)
const loadingDetail = ref(false)
const localError = ref('')
const detailTab = ref<DetailTab>('overview')

const name = ref('')
const description = ref('')
const services = ref<ServiceRow[]>([emptyService('default')])
const variables = ref<Pair[]>([])
const headers = ref<Pair[]>([{ key: 'Authorization', value: 'Bearer {{ZXBToken}}' }])
const secretRows = ref<Array<{ key: string; value: string; configured: boolean }>>([
  { key: 'ZXBToken', value: '', configured: false },
])

const feishuName = ref('API 基线报告')
const feishuWebhook = ref('')
const feishuEnabled = ref(false)

const sourceOptions = computed(() => context.sourceRevisions.filter(item => item.project_id === projectId.value))
const selectedProject = computed(() => context.projects.find(item => item.id === projectId.value) || null)
const selectedAsset = computed(() => setup.environmentAssets.find(item => item.id === selectedEnvironmentId.value) || null)
const environmentDetail = computed(() => setup.environment?.id === selectedEnvironmentId.value ? setup.environment : null)
const publicVariables = computed(() => Object.entries(environmentDetail.value?.variables || {}).filter(([, value]) => !isSecretDescriptor(value)))
const secretVariables = computed(() => Object.entries(environmentDetail.value?.variables || {}).filter(([, value]) => isSecretDescriptor(value)))
const projectEnvironmentStats = computed(() => setup.environmentProjectStats)

onMounted(async () => {
  await Promise.all([context.loadSavedContext(), context.loadOptions()])
  await refreshProjectEnvironmentStats()
  const queryProjectId = textQuery(route.query.projectId)
  projectId.value = context.projects.some(item => item.id === queryProjectId)
    ? queryProjectId
    : context.projectId || context.projects[0]?.id || ''
  await loadProjectScope()
})

async function loadProjectScope(): Promise<void> {
  localError.value = ''
  if (!projectId.value) {
    setup.environmentAssets = []
    clearSelection()
    return
  }
  loadingAssets.value = true
  try {
    await Promise.all([
      setup.loadEnvironmentAssets(projectId.value, environmentStatus.value),
      refreshProjectEnvironmentStats([projectId.value]),
      loadFeishu(),
    ])
    const preferredId = setup.environmentAssets.some(item => item.id === selectedEnvironmentId.value)
      ? selectedEnvironmentId.value
      : setup.environmentAssets[0]?.id || ''
    if (preferredId) await selectEnvironment(preferredId)
    else clearSelection()
  } catch (error) {
    localError.value = messageOf(error, '环境资产读取失败')
  } finally {
    loadingAssets.value = false
  }
}

async function changeProject(nextProjectId: string): Promise<void> {
  projectId.value = nextProjectId
  environmentStatus.value = 'active'
  selectedEnvironmentId.value = ''
  editing.value = false
  detailTab.value = 'overview'
  await loadProjectScope()
}

async function changeStatus(status: 'active' | 'archived'): Promise<void> {
  environmentStatus.value = status
  selectedEnvironmentId.value = ''
  editing.value = false
  detailTab.value = 'overview'
  await loadProjectScope()
}

async function selectEnvironment(environmentAssetId: string): Promise<void> {
  const asset = setup.environmentAssets.find(item => item.id === environmentAssetId)
  if (!asset) return
  selectedEnvironmentId.value = environmentAssetId
  sourceRevisionId.value = asset.source_revision_id || ''
  editing.value = false
  creating.value = false
  detailTab.value = 'overview'
  loadingDetail.value = true
  localError.value = ''
  try {
    const [detail] = await Promise.all([
      setup.loadEnvironmentRevision(asset.active_revision_id),
      setup.loadEnvironmentHistory(asset.id),
    ])
    applyEnvironment(detail)
  } catch (error) {
    localError.value = messageOf(error, '环境详情读取失败')
  } finally {
    loadingDetail.value = false
  }
}

function startEdit(): void {
  if (!environmentDetail.value) return
  applyEnvironment(environmentDetail.value)
  editing.value = true
  creating.value = false
  detailTab.value = 'overview'
}

async function startCreate(): Promise<void> {
  clearEditor()
  selectedEnvironmentId.value = ''
  sourceRevisionId.value = sourceOptions.value.at(-1)?.id || ''
  editing.value = true
  creating.value = true
  detailTab.value = 'overview'
  await prefillFromSource()
}

function cancelEdit(): void {
  editing.value = false
  detailTab.value = 'overview'
  if (selectedAsset.value && environmentDetail.value) applyEnvironment(environmentDetail.value)
  else if (!selectedAsset.value) clearEditor()
}

async function save(): Promise<void> {
  localError.value = ''
  if (!projectId.value || !name.value.trim()) {
    localError.value = '请选择项目并填写环境名称'
    return
  }
  const usableServices = services.value.filter(item => item.name.trim() || item.base_url.trim())
  if (!usableServices.length || usableServices.every(item => !item.base_url.trim())) {
    localError.value = '至少配置一个可用服务地址'
    return
  }
  const updatedSecretKeys = new Set(secretRows.value.filter(item => item.key.trim() && item.value).map(item => item.key.trim()))
  setup.secretUpdates = Object.fromEntries(
    secretRows.value.filter(item => item.key.trim() && item.value).map(item => [item.key.trim(), item.value]),
  )
  const payload: EnvironmentPayload = {
    project_id: projectId.value,
    source_id: sourceOptions.value.find(item => item.id === sourceRevisionId.value)?.source_id || null,
    source_revision_id: sourceRevisionId.value || null,
    name: name.value.trim(),
    description: description.value.trim(),
    services: Object.fromEntries(usableServices.map((item, index) => [
      item.key || item.name.trim() || `service-${index + 1}`,
      { name: item.name.trim() || item.key || `service-${index + 1}`, module_name: item.module.trim(), base_url: item.base_url.trim() || null },
    ])),
    variables: objectFromPairs(variables.value),
    default_headers: objectFromPairs(headers.value) as Record<string, string>,
  }
  try {
    const saved = await setup.saveEnvironment(creating.value ? null : environmentId.value, payload)
    secretRows.value.forEach(item => {
      if (updatedSecretKeys.has(item.key.trim())) item.configured = true
      item.value = ''
    })
    await context.loadOptions()
    environmentStatus.value = 'active'
    await setup.loadEnvironmentAssets(projectId.value, 'active')
    await refreshProjectEnvironmentStats([projectId.value])
    editing.value = false
    creating.value = false
    await selectEnvironment(saved.id)
  } catch (error) {
    localError.value = messageOf(error, '环境保存失败')
  }
}

async function archiveEnvironment(id: string): Promise<void> {
  if (!window.confirm('归档后该环境不会出现在工作台选择项中，历史版本和执行记录仍会保留。确定归档吗？')) return
  try {
    await setup.archiveEnvironment(id)
    await refreshProjectEnvironmentStats([projectId.value])
    await loadProjectScope()
  } catch (error) {
    localError.value = messageOf(error, '环境归档失败')
  }
}

async function restoreEnvironment(id: string): Promise<void> {
  try {
    await setup.restoreEnvironment(id)
    await refreshProjectEnvironmentStats([projectId.value])
    await loadProjectScope()
  } catch (error) {
    localError.value = messageOf(error, '环境恢复失败')
  }
}

async function restoreEnvironmentRevision(revisionId: string): Promise<void> {
  if (!window.confirm('确认将该历史版本恢复为新的当前版本吗？旧版本仍会保留。')) return
  loadingDetail.value = true
  localError.value = ''
  try {
    const restored = await setup.restoreEnvironmentRevision(revisionId)
    setup.replaceEnvironmentAsset(environmentAssetFromView(restored))
    await refreshProjectEnvironmentStats([projectId.value])
    setup.environmentHistory = [
      revisionSummaryFromView(restored),
      ...setup.environmentHistory.filter(item => item.id !== restored.revision_id),
    ]
    selectedEnvironmentId.value = restored.id
    sourceRevisionId.value = restored.source_revision_id || ''
    applyEnvironment(restored)
    detailTab.value = 'overview'
  } catch (error) {
    localError.value = messageOf(error, '环境历史版本恢复失败')
  } finally {
    loadingDetail.value = false
  }
}

async function openWorkbench(): Promise<void> {
  const asset = selectedAsset.value
  if (!asset) return
  const sourceRevision = asset.source_revision_id || sourceOptions.value.at(-1)?.id || ''
  if (!sourceRevision) {
    localError.value = '该环境尚未关联接口版本，请先前往接口资产同步最新接口'
    return
  }
  await router.push({
    name: 'workbench',
    query: {
      projectId: projectId.value,
      sourceRevisionId: sourceRevision,
      environmentRevisionId: asset.active_revision_id,
    },
  })
}

async function openSync(): Promise<void> {
  await router.push({ name: 'assets', query: { projectId: projectId.value } })
}

async function prefillFromSource(): Promise<void> {
  if (!sourceRevisionId.value) return
  try {
    const response = await apiClient.get<{ source_revision: SourceRevision }>(
      `/api/api-testing/v1/source-revisions/${sourceRevisionId.value}`,
    )
    const sourceServers = response.data.source_revision.normalized_document.servers
    const serverRows = Array.isArray(sourceServers) ? sourceServers as Array<Record<string, unknown>> : []
    const url = typeof serverRows[0]?.url === 'string' ? serverRows[0].url : ''
    if (url && !services.value.some(item => item.base_url)) services.value = [{ ...emptyService('default'), base_url: url }]
  } catch {
    // OpenAPI 可以没有 servers，环境仍允许手工配置。
  }
}

function applyEnvironment(value: EnvironmentView): void {
  environmentId.value = value.id
  name.value = value.name
  description.value = value.description
  services.value = Object.entries(value.services).map(([key, item], index) => ({
    key,
    name: serviceLabel(item, index),
    module: item.module_name && !isOpaqueId(item.module_name) ? item.module_name : '',
    base_url: item.base_url || '',
  }))
  if (!services.value.length) services.value = [emptyService('default')]
  variables.value = []
  secretRows.value = []
  for (const [key, raw] of Object.entries(value.variables)) {
    if (isSecretDescriptor(raw)) secretRows.value.push({ key, value: '', configured: raw.configured === true })
    else variables.value.push({ key, value: displayValue(raw) })
  }
  if (!secretRows.value.length) secretRows.value.push({ key: 'ZXBToken', value: '', configured: false })
  headers.value = Object.entries(value.default_headers).map(([key, raw]) => ({ key, value: String(raw ?? '') }))
}

function clearSelection(): void {
  selectedEnvironmentId.value = ''
  setup.environment = null
  setup.environmentHistory = []
  editing.value = false
  creating.value = false
  detailTab.value = 'overview'
  clearEditor()
}

function clearEditor(): void {
  environmentId.value = null
  name.value = ''
  description.value = ''
  services.value = [emptyService('default')]
  variables.value = []
  headers.value = [{ key: 'Authorization', value: 'Bearer {{ZXBToken}}' }]
  secretRows.value = [{ key: 'ZXBToken', value: '', configured: false }]
}

async function loadFeishu(): Promise<void> {
  if (!projectId.value) return
  await notifications.loadFeishu(projectId.value)
  const current = notifications.feishu
  feishuName.value = current?.name || 'API 基线报告'
  feishuEnabled.value = current?.enabled === true
  feishuWebhook.value = ''
}

async function saveFeishu(): Promise<void> {
  localError.value = ''
  if (!projectId.value) {
    localError.value = '请先选择项目'
    return
  }
  try {
    await notifications.saveFeishu(projectId.value, {
      name: feishuName.value.trim() || 'API 基线报告',
      enabled: feishuEnabled.value,
      webhook: feishuWebhook.value.trim(),
    })
    feishuWebhook.value = ''
  } catch (error) {
    localError.value = messageOf(error, '飞书通知保存失败')
  }
}

function sourceLabel(sourceId: string | null): string {
  if (!sourceId) return '未绑定接口版本'
  const source = context.sourceRevisions.find(item => item.id === sourceId)
  return source ? `${source.name} · v${source.revision_number}` : '历史接口版本'
}

async function refreshProjectEnvironmentStats(projectIds = context.projects.map(item => item.id)): Promise<void> {
  const ids = projectIds.filter(Boolean)
  if (!ids.length) return
  await setup.loadEnvironmentProjectStats(ids)
}

function serviceLabel(item: { name: string; module_name?: string }, index: number): string {
  if (item.module_name && !isOpaqueId(item.module_name)) return item.module_name
  if (item.name && !isOpaqueId(item.name) && item.name !== 'default') return item.name
  return item.name === 'default' ? '默认服务' : `服务 ${index + 1}`
}

function environmentAssetFromView(value: EnvironmentView): EnvironmentAsset {
  return {
    id: value.id,
    project_id: value.project_id,
    source_id: value.source_id,
    active_revision_id: value.revision_id,
    source_revision_id: value.source_revision_id,
    revision: value.revision,
    name: value.name,
    description: value.description,
    status: value.status === 'archived' ? 'archived' : 'active',
    service_count: Object.keys(value.services).length,
    public_variable_count: Object.values(value.variables).filter(item => !isSecretDescriptor(item)).length,
    secret_count: Object.values(value.variables).filter(item => isSecretDescriptor(item)).length,
    created_at: new Date().toISOString(),
    updated_at: new Date().toISOString(),
  }
}

function revisionSummaryFromView(value: EnvironmentView): EnvironmentRevisionSummary {
  return {
    id: value.revision_id,
    environment_id: value.id,
    source_revision_id: value.source_revision_id,
    revision: value.revision,
    name: value.name,
    description: value.description,
    status: value.status,
    created_at: new Date().toISOString(),
    updated_at: new Date().toISOString(),
  }
}

function emptyService(key: string): ServiceRow {
  return { key, name: key === 'default' ? '默认服务' : '', module: key === 'default' ? '默认服务' : '', base_url: '' }
}

function addPair(rows: Pair[]): void { rows.push({ key: '', value: '' }) }
function objectFromPairs(rows: Pair[]): Record<string, unknown> {
  return Object.fromEntries(rows.filter(item => item.key.trim()).map(item => [item.key.trim(), parseValue(item.value)]))
}
function parseValue(value: string): unknown { try { return JSON.parse(value) } catch { return value } }
function displayValue(value: unknown): string { return typeof value === 'string' ? value : JSON.stringify(value) }
function isSecretDescriptor(value: unknown): value is { configured: boolean } { return Boolean(value && typeof value === 'object' && 'configured' in value) }
function isOpaqueId(value: string): boolean { return /^[0-9a-f]{8}-[0-9a-f-]{27,}$/i.test(value) }
function textQuery(value: unknown): string { return typeof value === 'string' ? value : '' }
function messageOf(error: unknown, fallback: string): string { return error instanceof Error ? error.message : fallback }
function formatDate(value: string): string { return value ? new Date(value).toLocaleString('zh-CN', { hour12: false }) : '未知时间' }
</script>

<template>
  <section class="workspace environment-center-page">
    <header class="page-toolbar environment-page-toolbar">
      <div>
        <p class="eyebrow">API ENVIRONMENT ASSETS</p>
        <h1>项目环境</h1>
        <p class="page-subtitle">环境按项目长期保存；同步、编辑和运行互不混淆，历史版本可追溯。</p>
      </div>
      <div class="toolbar-actions">
        <button class="secondary-command" type="button" :disabled="!projectId" data-action="sync" @click="openSync">前往同步</button>
        <button class="primary-command" type="button" :disabled="!projectId" data-action="create" @click="startCreate"><Plus :size="15" />新建环境</button>
      </div>
    </header>

    <section class="environment-center-shell">
      <EnvironmentAssetList
        :projects="context.projects"
        :environments="setup.environmentAssets"
        :selected-project-id="projectId"
        :selected-environment-id="selectedEnvironmentId"
        :status="environmentStatus"
        :project-stats="projectEnvironmentStats"
        @select-project="changeProject"
        @select-environment="selectEnvironment"
        @update:status="changeStatus"
        @archive="archiveEnvironment"
        @restore="restoreEnvironment"
      />

      <main class="environment-detail-panel">
        <div v-if="loadingAssets || loadingDetail" class="environment-detail-empty">正在读取环境资产…</div>

        <template v-else-if="editing">
          <header class="environment-detail-header">
            <div><p class="eyebrow">{{ creating ? 'NEW ENVIRONMENT' : 'NEW REVISION' }}</p><h2>{{ creating ? '新建环境' : `编辑 ${selectedAsset?.name || name}` }}</h2><p>{{ creating ? '新环境保存后归属于当前项目。' : '保存会创建新版本，旧版本保留在历史中。' }}</p></div>
            <div class="toolbar-actions"><button class="secondary-command" type="button" @click="cancelEdit">取消</button><button class="primary-command" type="button" :disabled="setup.busy" data-action="save" @click="save"><Save :size="15" />{{ setup.busy ? '正在保存' : (creating ? '保存环境' : '保存新版本') }}</button></div>
          </header>

          <section class="environment-editor-section">
            <header><div><h3>基本信息</h3><p>工作台、任务和报告只展示业务名称，不展示数据库 ID。</p></div></header>
            <div class="setup-grid three">
              <label>所属项目<input :value="selectedProject?.name || ''" disabled /></label>
              <label>环境名称<input v-model="name" placeholder="例如：生产环境（新）- 腾讯云" /></label>
              <label>来源接口版本<select v-model="sourceRevisionId" @change="prefillFromSource"><option value="">不绑定接口版本</option><option v-for="item in sourceOptions" :key="item.id" :value="item.id">{{ item.name }} · v{{ item.revision_number }}</option></select></label>
            </div>
            <label class="wide-field">说明<input v-model="description" placeholder="例如：线上发布后的基线回归环境" /></label>
          </section>

          <section class="environment-editor-section">
            <header><div><h3>服务地址</h3><p>内部服务键只用于执行匹配；页面、工作台和报告展示业务名称或模块。</p></div><button class="mini-icon" type="button" title="添加服务" @click="services.push(emptyService(`service-${services.length + 1}`))"><Plus :size="15" /></button></header>
            <div class="editable-table"><div class="table-head"><span>服务名称</span><span>模块</span><span>Base URL</span><span></span></div><div v-for="(item, index) in services" :key="item.key || index" class="table-row service-row"><input v-model="item.name" aria-label="服务名" /><input v-model="item.module" aria-label="服务模块" /><input v-model="item.base_url" aria-label="服务地址" placeholder="https://api.example.com" /><button class="mini-icon danger" type="button" title="删除服务" @click="services.splice(index, 1)"><Trash2 :size="14" /></button></div></div>
          </section>

          <section class="environment-editor-section split-section">
            <div><header><div><h3>公共变量</h3><p>普通值可回显和编辑。</p></div><button class="mini-icon" type="button" title="添加变量" @click="addPair(variables)"><Plus :size="15" /></button></header><div class="pair-list"><div v-for="(item, index) in variables" :key="index" class="pair-row"><input v-model="item.key" aria-label="变量名" placeholder="变量名" /><input v-model="item.value" aria-label="变量值" placeholder="变量值" /><button class="mini-icon danger" type="button" title="删除变量" @click="variables.splice(index, 1)"><Trash2 :size="14" /></button></div><p v-if="!variables.length" class="compact-empty">暂无公共变量</p></div></div>
            <div><header><div><h3>敏感变量</h3><p>只显示配置状态，新值提交后立即清空。</p></div><button class="mini-icon" type="button" title="添加敏感变量" @click="secretRows.push({ key: '', value: '', configured: false })"><Plus :size="15" /></button></header><div class="pair-list"><div v-for="(item, index) in secretRows" :key="index" class="pair-row secret-row"><input v-model="item.key" aria-label="敏感变量名" placeholder="例如 ZXBToken" /><input v-model="item.value" type="password" autocomplete="new-password" aria-label="敏感变量值" :placeholder="item.configured ? '已配置，留空则保持不变' : '输入后仅发送一次'" /><KeyRound :size="15" :class="item.configured ? 'secret-configured' : 'secret-empty'" /></div></div></div>
          </section>

          <section class="environment-editor-section">
            <header><div><h3>默认请求头</h3><p>推荐引用变量，例如 Authorization: Bearer &#123;&#123;ZXBToken&#125;&#125;。</p></div><button class="mini-icon" type="button" title="添加请求头" @click="addPair(headers)"><Plus :size="15" /></button></header><div class="pair-list"><div v-for="(item, index) in headers" :key="index" class="pair-row"><input v-model="item.key" aria-label="请求头名称" /><input v-model="item.value" aria-label="请求头值" /><button class="mini-icon danger" type="button" title="删除请求头" @click="headers.splice(index, 1)"><Trash2 :size="14" /></button></div></div>
          </section>
        </template>

        <template v-else-if="selectedAsset && environmentDetail">
          <header class="environment-detail-header">
            <div><p class="eyebrow">ENVIRONMENT</p><h2>{{ selectedAsset.name }}</h2><p>{{ selectedAsset.description || '暂无说明' }}</p></div>
            <div class="toolbar-actions"><button class="secondary-command" type="button" data-action="edit" @click="startEdit"><Pencil :size="15" />编辑</button><button class="primary-command" type="button" data-action="workbench" @click="openWorkbench">进入工作台<ArrowRight :size="15" /></button></div>
          </header>

          <div class="environment-overview-strip">
            <div><small>所属项目</small><strong>{{ selectedProject?.name }}</strong></div>
            <div><small>当前版本</small><strong>v{{ selectedAsset.revision }}</strong></div>
            <div><small>来源接口</small><strong>{{ sourceLabel(selectedAsset.source_revision_id) }}</strong></div>
            <div><small>最近更新</small><strong>{{ formatDate(selectedAsset.updated_at) }}</strong></div>
          </div>

          <nav class="environment-detail-tabs" aria-label="环境详情">
            <button
              v-for="tab in detailTabs"
              :key="tab.id"
              type="button"
              :class="{ active: detailTab === tab.id }"
              :data-tab="tab.id"
              @click="detailTab = tab.id"
            >{{ tab.label }}</button>
          </nav>

          <section v-if="detailTab === 'overview'" class="environment-read-section environment-overview-detail">
            <header><div><h3>环境概览</h3><p>环境资产独立于接口版本长期保存；调试或任务执行时再选择当前环境版本。</p></div></header>
            <div class="environment-summary-grid">
              <div><small>状态</small><strong>{{ selectedAsset.status === 'active' ? '使用中' : '已归档' }}</strong></div>
              <div><small>服务地址</small><strong>{{ selectedAsset.service_count }}</strong></div>
              <div><small>公共变量</small><strong>{{ selectedAsset.public_variable_count }}</strong></div>
              <div><small>敏感凭证</small><strong>{{ selectedAsset.secret_count }}</strong></div>
            </div>
            <div v-if="Object.keys(environmentDetail.services).length" class="environment-overview-services">
              <article v-for="(item, key, index) in environmentDetail.services" :key="key">
                <span :title="serviceLabel(item, index)">{{ serviceLabel(item, index) }}</span>
                <code :title="item.base_url || '未配置地址'">{{ item.base_url || '未配置地址' }}</code>
              </article>
            </div>
          </section>

          <section v-else-if="detailTab === 'services'" class="environment-read-section">
            <header><div><h3>服务地址</h3><p>{{ selectedAsset.service_count }} 个服务可用于在线调试和任务执行。</p></div></header>
            <div class="environment-service-list"><article v-for="(item, key, index) in environmentDetail.services" :key="key"><div><strong>{{ serviceLabel(item, index) }}</strong><small>{{ key === 'default' ? '默认服务' : '按内部键匹配执行' }}</small></div><code>{{ item.base_url || '未配置地址' }}</code></article></div>
          </section>

          <section v-else-if="detailTab === 'variables'" class="environment-read-grid">
            <div class="environment-read-section"><header><div><h3>公共变量</h3><p>执行时按变量名注入。</p></div><span>{{ publicVariables.length }}</span></header><dl><template v-for="([key, value]) in publicVariables" :key="key"><dt>{{ key }}</dt><dd>{{ displayValue(value) || '空值' }}</dd></template></dl><p v-if="!publicVariables.length" class="compact-empty">暂无公共变量</p></div>
            <div class="environment-read-section"><header><div><h3>敏感凭证</h3><p>页面不回显密文。</p></div><span>{{ secretVariables.length }}</span></header><dl><template v-for="([key, value]) in secretVariables" :key="key"><dt>{{ key }}</dt><dd><Check v-if="isSecretDescriptor(value) && value.configured" :size="14" />{{ isSecretDescriptor(value) && value.configured ? '已配置' : '未配置' }}</dd></template></dl><p v-if="!secretVariables.length" class="compact-empty">暂无敏感凭证</p></div>
            <div class="environment-read-section environment-header-section"><header><div><h3>默认请求头</h3><p>执行时自动注入，推荐使用变量引用。</p></div><span>{{ Object.keys(environmentDetail.default_headers).length }}</span></header><dl><template v-for="([key, value]) in Object.entries(environmentDetail.default_headers)" :key="key"><dt>{{ key }}</dt><dd>{{ displayValue(value) }}</dd></template></dl><p v-if="!Object.keys(environmentDetail.default_headers).length" class="compact-empty">暂无默认请求头</p></div>
          </section>

          <section v-else class="environment-read-section environment-history">
            <header><div><h3>版本历史</h3><p>编辑环境会新增版本，历史不会被覆盖。</p></div><span>{{ setup.environmentHistory.length }} 个版本</span></header>
            <ol><li v-for="revision in setup.environmentHistory" :key="revision.id"><span class="history-version">v{{ revision.revision }}</span><div><strong>{{ revision.name }}</strong><small>{{ sourceLabel(revision.source_revision_id) }} · {{ formatDate(revision.updated_at) }}</small></div><span v-if="revision.id === selectedAsset.active_revision_id" class="current-version">当前</span><button v-else class="environment-history-action" type="button" :data-revision-id="revision.id" data-action="restore-revision" @click="restoreEnvironmentRevision(revision.id)"><RotateCcw :size="14" />恢复</button></li></ol>
          </section>
        </template>

        <div v-else class="environment-detail-empty"><strong>{{ environmentStatus === 'active' ? '当前项目还没有环境资产' : '当前项目没有已归档环境' }}</strong><span>{{ environmentStatus === 'active' ? '从 Apifox 手动同步，或新建一个可调试环境。' : '归档环境会保留历史，之后可以恢复。' }}</span></div>
      </main>
    </section>

    <section v-if="projectId" class="setup-section notification-section project-notification-card">
      <header><div><p class="eyebrow">PROJECT NOTIFICATION</p><h2>项目飞书通知</h2><p>机器人绑定到 {{ selectedProject?.name }}；后续定时任务只保存“是否通知”，不重复保存 Webhook。</p></div><span v-if="notifications.feishu?.configured" class="configured-state"><Check :size="14" />已配置 {{ notifications.feishu.fingerprint }}</span></header>
      <div class="setup-grid three"><label>通知名称<input v-model="feishuName" placeholder="例如：API 基线报告" /></label><label class="grow">飞书群机器人 Webhook<input v-model="feishuWebhook" type="password" autocomplete="new-password" placeholder="已配置时留空表示保持不变" /></label><label class="toggle-card"><input v-model="feishuEnabled" type="checkbox" />启用报告发送</label></div>
      <footer class="notification-actions"><span><Bell :size="14" />任务和基线是独立资产；后续调度器可选择任务或基线分组，并决定是否发送飞书。</span><button class="secondary-command" type="button" :disabled="notifications.loading" @click="loadFeishu">{{ notifications.loading ? '读取中' : '读取配置' }}</button><button class="primary-command" type="button" :disabled="notifications.saving" @click="saveFeishu">{{ notifications.saving ? '保存中' : '保存项目通知' }}</button></footer>
    </section>

    <p v-if="localError || setup.error || context.error || notifications.error" class="inline-error" role="alert">{{ localError || setup.error || context.error || notifications.error }}</p>
    <p v-if="setup.message" class="setup-success"><Check :size="16" />{{ setup.message }}</p>
    <p v-if="notifications.message" class="setup-success"><Bell :size="16" />{{ notifications.message }}</p>
  </section>
</template>
