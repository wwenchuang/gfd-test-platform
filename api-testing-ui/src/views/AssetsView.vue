<script setup lang="ts">
import { computed, nextTick, onMounted, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import {
  ArrowRight, Check, ClipboardList, CloudDownload, Database,
  Edit3, FileJson, FolderPlus, KeyRound, Layers, RefreshCw, Save, Trash2,
  Upload,
} from 'lucide-vue-next'

import type { EnvironmentRevisionOption, SourcePreview, SourceRevisionOption } from '../api/contracts'
import { useAssetsStore } from '../stores/assets'
import { useContextStore } from '../stores/context'
import { useSetupStore } from '../stores/setup'

const context = useContextStore()
const assets = useAssetsStore()
const setup = useSetupStore()
const route = useRoute()
const projectId = ref('')
const revisionId = ref('')
const environmentRevisionId = ref('')
const projectName = ref('')
const showProjectForm = ref(false)
const showProjectEditor = ref(false)
const projectEditName = ref('')
const projectEditDescription = ref('')
const apifoxToken = ref('')
const apifoxProjectId = ref('')
const branchId = ref('')
const environmentId = ref('')
const selectedFile = ref<File | null>(null)
const fileName = ref('')
const localError = ref('')
const syncOpen = ref(false)
const syncPanel = ref<HTMLDetailsElement | null>(null)
const changeSearch = ref('')
const savedPreview = ref<{ sourceId: string; preview: SourcePreview } | null>(null)

const selectedProject = computed(() => context.projects.find(item => item.id === projectId.value) || null)
const revisions = computed(() => context.sourceRevisions.filter(item => item.project_id === projectId.value))
const environments = computed(() => context.environmentRevisions.filter(item => item.project_id === projectId.value))
const currentRevision = computed(() => revisions.value.find(item => item.id === revisionId.value) || null)
const currentEnvironmentRevision = computed(() => environments.value.find(item => item.id === environmentRevisionId.value) || null)
const selectedEnvironment = computed(() => setup.apifoxContext?.environments.find(item => item.id === environmentId.value) || null)
const apifoxEnvironmentNameCounts = computed(() => {
  const counts = new Map<string, number>()
  for (const item of setup.apifoxContext?.environments || []) {
    counts.set(item.name, (counts.get(item.name) || 0) + 1)
  }
  return counts
})
const canCheckUpdate = computed(() => Boolean(projectId.value && apifoxProjectId.value && environmentId.value))
const workbenchLink = computed(() => {
  if (!selectedProject.value || !currentRevision.value || !currentEnvironmentRevision.value) return null
  return {
    path: '/',
    query: {
      projectId: selectedProject.value.id,
      sourceRevisionId: currentRevision.value.id,
      environmentRevisionId: currentEnvironmentRevision.value.id,
    },
  }
})
const casesLink = computed(() => workbenchLink.value ? {
  path: '/cases', query: workbenchLink.value.query,
} : null)
const projectCards = computed(() => context.projects.map(project => {
  const sourceOptions = context.sourceRevisions.filter(item => item.project_id === project.id)
  const environmentOptions = context.environmentRevisions.filter(item => item.project_id === project.id)
  const latest = latestSourceRevision(sourceOptions)
  return {
    project,
    latest,
    endpointCount: latest?.endpoint_count || 0,
    environmentCount: environmentOptions.length,
    lastSync: latest?.activated_at || latest?.created_at || project.updated_at || project.created_at || '',
  }
}))
const displayedPreview = computed(() => setup.preview || (savedPreview.value?.sourceId === revisionId.value ? savedPreview.value.preview : null))
const diffSummary = computed(() => displayedPreview.value ? [
  { label: '新增', value: displayedPreview.value.added_count },
  { label: '变更', value: displayedPreview.value.changed_count },
  { label: '删除', value: displayedPreview.value.removed_count },
] : [
  { label: '新增', value: '-' },
  { label: '变更', value: '-' },
  { label: '删除', value: '-' },
])
const visibleChanges = computed(() => (displayedPreview.value?.changes || []).filter(change =>
  `${change.method} ${change.path}`.toLowerCase().includes(changeSearch.value.trim().toLowerCase()),
))
const changeLabels: Record<string, string> = { added: '新增', changed: '变更', removed: '删除' }
const fieldLabels: Record<string, string> = {
  responses: '响应定义', parameters: '请求参数', requestBody: '请求体', summary: '接口名称',
  description: '说明', tags: '分组', security: '鉴权', method: '请求方式', path: '路径', operationId: '接口标识', operation_id: '接口标识',
}
function changedFields(change: Record<string, unknown>): string {
  return Array.isArray(change.changed_fields) ? change.changed_fields.map(key => fieldLabels[String(key)] || String(key)).join('、') : ''
}
function apifoxBranchLabel(item: { id: string; name: string; is_default: boolean }): string {
  return item.is_default && !item.id ? `${item.name} · 项目默认入口` : item.name
}
function apifoxEnvironmentLabel(item: { id: string; name: string }): string {
  if ((apifoxEnvironmentNameCounts.value.get(item.name) || 0) <= 1) return item.name
  return `${item.name} · ID ${item.id.slice(-6)}`
}
function savedEndpointLink(change: Record<string, unknown>) {
  if (setup.preview || !workbenchLink.value || setup.activeRevision?.id !== revisionId.value) return null
  const endpoint = assets.endpoints.find(item => item.method === change.method && item.path === change.path)
  return endpoint ? { ...workbenchLink.value, query: { ...workbenchLink.value.query, endpointId: endpoint.id } } : null
}
async function openSync(): Promise<void> {
  syncOpen.value = true
  await nextTick()
  syncPanel.value?.scrollIntoView?.({ behavior: 'smooth', block: 'start' })
}
watch(apifoxProjectId, () => {
  setup.apifoxContext = null
  branchId.value = ''
  environmentId.value = ''
})
watch([apifoxProjectId, branchId, environmentId, projectId, revisionId], () => {
  setup.preview = null
  setup.apifoxPreview = null
  changeSearch.value = ''
})

onMounted(async () => {
  await Promise.all([context.loadSavedContext(), context.loadOptions(), setup.loadApifoxCredential()])
  const requestedProjectId = routeValue(route.query.projectId)
  projectId.value = context.projects.some(item => item.id === requestedProjectId)
    ? requestedProjectId
    : context.projectId || context.projects[0]?.id || ''
  chooseDefaultContext()
  syncOpen.value = !currentRevision.value
})

function routeValue(value: unknown): string {
  return typeof value === 'string' ? value : ''
}

function latestSourceRevision(items: SourceRevisionOption[]): SourceRevisionOption | null {
  return [...items].sort((left, right) => left.revision_number - right.revision_number || left.id.localeCompare(right.id)).at(-1) || null
}

function latestEnvironmentRevision(items: EnvironmentRevisionOption[]): EnvironmentRevisionOption | null {
  return [...items].sort((left, right) => left.revision - right.revision || left.id.localeCompare(right.id)).at(-1) || null
}

function chooseDefaultContext(): void {
  const sourceOptions = context.sourceRevisions.filter(item => item.project_id === projectId.value)
  const environmentOptions = context.environmentRevisions.filter(item => item.project_id === projectId.value)
  revisionId.value = sourceOptions.some(item => item.id === context.sourceRevisionId)
    ? context.sourceRevisionId || '' : latestSourceRevision(sourceOptions)?.id || ''
  environmentRevisionId.value = environmentOptions.some(item => item.id === context.environmentRevisionId)
    ? context.environmentRevisionId || '' : latestEnvironmentRevision(environmentOptions)?.id || ''
  setup.apifoxPreview = null
  setup.preview = null
}

function selectProject(id: string): void {
  projectId.value = id
  chooseDefaultContext()
}

function openProjectEditor(): void {
  if (!selectedProject.value) return
  localError.value = ''
  setup.message = ''
  projectEditName.value = selectedProject.value.name
  projectEditDescription.value = selectedProject.value.description || ''
  showProjectEditor.value = true
}

async function saveProjectEdit(): Promise<void> {
  localError.value = ''
  setup.message = ''
  if (!selectedProject.value) return
  if (!projectEditName.value.trim()) { localError.value = '请输入项目名称'; return }
  try {
    await setup.updateProject(selectedProject.value.id, {
      name: projectEditName.value,
      description: projectEditDescription.value,
    })
    await context.loadOptions()
    showProjectEditor.value = false
  } catch (error) { localError.value = error instanceof Error ? error.message : '项目信息保存失败' }
}

function cancelProjectEdit(): void {
  showProjectEditor.value = false
  localError.value = ''
  setup.message = ''
}

async function archiveProject(): Promise<void> {
  localError.value = ''
  if (!selectedProject.value) return
  if (!window.confirm(`归档项目“${selectedProject.value.name}”？已有任务、基线和执行记录不会被物理删除。`)) return
  try {
    await setup.archiveProject(selectedProject.value.id)
    await context.loadOptions()
    projectId.value = context.projects[0]?.id || ''
    chooseDefaultContext()
  } catch (error) { localError.value = error instanceof Error ? error.message : '项目归档失败' }
}

async function createProject(): Promise<void> {
  localError.value = ''
  if (!projectName.value.trim()) { localError.value = '请输入项目名称'; return }
  try {
    const createdProjectId = await setup.createProject(projectName.value)
    await context.loadOptions()
    projectId.value = createdProjectId
    projectName.value = ''
    showProjectForm.value = false
    chooseDefaultContext()
  } catch (error) { localError.value = error instanceof Error ? error.message : '项目创建失败' }
}

async function saveToken(): Promise<void> {
  localError.value = ''
  if (!apifoxToken.value.trim()) { localError.value = '请输入 Apifox 访问令牌'; return }
  try {
    await setup.saveApifoxToken(apifoxToken.value)
    apifoxToken.value = ''
  } catch (error) { localError.value = error instanceof Error ? error.message : '访问令牌保存失败' }
}

async function readProjects(): Promise<void> {
  localError.value = ''
  try {
    const projects = await setup.discoverApifoxProjects()
    apifoxProjectId.value = projects.some(item => item.id === apifoxProjectId.value)
      ? apifoxProjectId.value : projects.length === 1 ? projects[0].id : ''
    setup.apifoxContext = null
    branchId.value = ''
    environmentId.value = ''
  } catch (error) { localError.value = error instanceof Error ? error.message : 'Apifox 项目读取失败' }
}

async function readContext(): Promise<void> {
  localError.value = ''
  if (!apifoxProjectId.value) { localError.value = '请先选择 Apifox 项目'; return }
  try {
    const result = await setup.discoverApifoxContext(apifoxProjectId.value)
    branchId.value = result.branches.find(item => item.is_default)?.id || result.branches[0]?.id || ''
    const matching = result.environments.filter(item => item.name === currentEnvironmentRevision.value?.name)
    environmentId.value = result.environments.some(item => item.id === environmentId.value)
      ? environmentId.value : matching.length === 1 ? matching[0].id : result.environments.length === 1 ? result.environments[0].id : ''
  } catch (error) { localError.value = error instanceof Error ? error.message : 'Apifox 环境读取失败' }
}

async function checkApifoxUpdate(): Promise<void> {
  localError.value = ''
  if (!projectId.value) { localError.value = '请先选择平台项目'; return }
  if (!canCheckUpdate.value) { localError.value = '请选择 Apifox 项目和环境'; return }
  try {
    await setup.previewApifox({
      project_id: projectId.value,
      source_id: currentRevision.value?.source_id || null,
      apifox_project_id: apifoxProjectId.value,
      branch_id: branchId.value,
      environment_id: environmentId.value,
    })
  } catch (error) { localError.value = error instanceof Error ? error.message : 'Apifox 更新检查失败' }
}

async function saveApifoxUpdate(): Promise<void> {
  localError.value = ''
  const preview = setup.preview
  try {
    const result = await setup.activateApifoxPreview()
    context.applyWorkspace(result.workspace)
    await context.loadOptions()
    projectId.value = result.workspace.project_id || projectId.value
    revisionId.value = result.source_revision.id
    environmentRevisionId.value = result.environment.revision_id
    await assets.load(result.source_revision.id)
    if (preview) savedPreview.value = { sourceId: result.source_revision.id, preview }
  } catch (error) { localError.value = error instanceof Error ? error.message : 'Apifox 更新保存失败' }
}

function pickFile(event: Event): void {
  const file = (event.target as HTMLInputElement).files?.[0] || null
  selectedFile.value = file
  fileName.value = file?.name || ''
  setup.preview = null
  setup.apifoxPreview = null
  setup.message = ''
}

async function readSource(): Promise<void> {
  localError.value = ''
  if (!projectId.value) { localError.value = '请先选择项目'; return }
  if (!selectedFile.value) { localError.value = '请选择 OpenAPI JSON 文件'; return }
  try {
    const document = JSON.parse(await selectedFile.value.text()) as Record<string, unknown>
    await setup.previewSource(projectId.value, currentRevision.value?.source_id || null, document)
    await openSync()
  } catch (error) {
    localError.value = error instanceof SyntaxError ? '接口文件不是有效的 JSON' : error instanceof Error ? error.message : '接口文件读取失败'
  }
}

async function saveJsonRevision(): Promise<void> {
  localError.value = ''
  const preview = setup.preview
  let revision
  try {
    revision = await setup.activatePreview()
    selectedFile.value = null
    fileName.value = ''
  } catch (error) {
    localError.value = error instanceof Error ? error.message : '接口版本保存失败'
    return
  }
  try {
    await context.loadOptions()
    projectId.value = revision.project_id || projectId.value
    revisionId.value = revision.id
    if (preview) savedPreview.value = { sourceId: revision.id, preview }
    const projectEnvironments = context.environmentRevisions.filter(
      item => item.project_id === projectId.value,
    )
    if (!projectEnvironments.some(item => item.id === environmentRevisionId.value)) {
      environmentRevisionId.value = latestEnvironmentRevision(projectEnvironments)?.id || ''
    }
    context.selectProject(projectId.value || null)
    context.selectSourceRevision(revision.id)
    context.selectEnvironmentRevision(environmentRevisionId.value || null)
    await context.saveContext()
    if (context.error) throw new Error(context.error)
    setup.message = `接口版本 v${revision.revision_number} 已保存并切换为当前测试范围`
  } catch (error) {
    localError.value = `接口版本 v${revision.revision_number} 已保存，但自动切换测试范围失败：${error instanceof Error ? error.message : '请手动选择新版本'}`
  }
}

function dateText(value?: string | null): string {
  if (!value) return '暂无'
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return value
  return date.toLocaleString('zh-CN', { hour12: false })
}
</script>

<template>
  <section class="workspace setup-page">
    <header class="page-toolbar">
      <div>
        <p class="eyebrow">接口资产</p>
        <h1>接口资产</h1>
        <p class="page-subtitle">已有接口可直接进入工作台。需要更新时，再展开 Apifox 同步；接口版本与执行环境分别保存。</p>
      </div>
    </header>

    <section class="api-asset-shell">
      <aside class="api-asset-projects">
        <header>
          <div>
            <p class="eyebrow">项目列表</p>
            <h2>项目资产</h2>
          </div>
          <button
            class="icon-command"
            type="button"
            title="打开项目创建面板"
            aria-label="打开项目创建面板"
            @click="showProjectForm = !showProjectForm"
          >
            <FolderPlus :size="17" />
          </button>
        </header>
        <div v-if="showProjectForm" class="inline-create compact">
          <label>项目名称<input v-model="projectName" placeholder="例如：3D 家用业务" /></label>
          <button class="primary-command" type="button" @click="createProject">创建</button>
        </div>
        <button
          v-for="card in projectCards"
          :key="card.project.id"
          class="asset-project-card"
          :class="{ active: card.project.id === projectId }"
          type="button"
          :disabled="setup.busy"
          @click="selectProject(card.project.id)"
        >
          <span class="project-name">{{ card.project.name }}</span>
          <span class="project-meta">{{ card.endpointCount }} 个接口 · {{ card.environmentCount }} 个环境</span>
          <span class="project-sync">最近同步：{{ dateText(card.lastSync) }}</span>
          <span class="project-binding">接口来源：{{ card.latest?.name || '未绑定' }}</span>
        </button>
      </aside>

      <section class="api-asset-detail">
        <header>
          <div>
            <p class="eyebrow">当前使用的数据</p>
            <h2>{{ selectedProject?.name || '尚未选择项目' }}</h2>
            <p>{{ selectedProject?.description || '选择项目后查看接口版本、分组、环境和变更摘要。' }}</p>
          </div>
          <span class="asset-state"><Database :size="16" />{{ currentRevision ? '已保存接口资产' : '尚未同步接口' }}</span>
        </header>

        <div class="asset-version-grid">
          <label>接口版本
            <select v-model="revisionId" data-testid="saved-source" :disabled="setup.busy || !revisions.length">
              <option value="">暂无接口版本</option>
              <option v-for="item in revisions" :key="item.id" :value="item.id">
                {{ item.name }} · v{{ item.revision_number }} · {{ item.endpoint_count }} 个接口
              </option>
            </select>
          </label>
          <label>已保存执行环境
            <select v-model="environmentRevisionId" data-testid="saved-environment" :disabled="setup.busy || !environments.length">
              <option value="">暂无环境</option>
              <option v-for="item in environments" :key="item.id" :value="item.id">
                {{ item.name }} · v{{ item.revision }}
              </option>
            </select>
          </label>
        </div>

        <div class="asset-kpis">
          <div><span>接口数量</span><strong>{{ currentRevision?.endpoint_count || 0 }}</strong><small>工作台只加载该版本</small></div>
          <div><span>最近同步</span><strong>{{ dateText(currentRevision?.activated_at || currentRevision?.created_at) }}</strong><small>手动同步生成新版本</small></div>
        </div>

        <p class="asset-next-note" data-testid="asset-next-step">
          <template v-if="!currentRevision">下一步：展开下方同步，读取 Apifox 接口；也可以导入 OpenAPI JSON 文件。</template>
          <template v-else-if="!currentEnvironmentRevision">接口已保存。下一步：<RouterLink :to="{ path: '/settings', query: { projectId } }">配置执行环境</RouterLink>，再进入工作台。</template>
          <template v-else>下一步：进入工作台选接口、生成和调试用例。历史用例仍可从“用例管理”查看，切换版本不会删除它们。</template>
        </p>
      </section>

      <aside class="api-asset-actions">
        <header>
          <p class="eyebrow">开始测试</p>
          <h2>下一步</h2>
        </header>
        <RouterLink v-if="workbenchLink" class="primary-command wide" :to="workbenchLink">
          <span>进入工作台</span><ArrowRight :size="16" />
        </RouterLink>
        <button v-else class="primary-command wide" type="button" disabled>进入工作台</button>
        <RouterLink v-if="casesLink" class="secondary-command wide" :to="casesLink">
          <ClipboardList :size="16" /><span>进入用例管理</span>
        </RouterLink>
        <button class="secondary-command wide" type="button" :disabled="setup.busy" data-testid="open-apifox-sync" @click="openSync"><RefreshCw :size="16" />同步接口更新</button>
        <details class="project-maintenance"><summary>项目设置</summary>
          <button class="secondary-command wide" type="button" :disabled="setup.busy || !selectedProject" @click="openProjectEditor"><Edit3 :size="16" />编辑项目</button>
          <button class="secondary-command wide danger" type="button" :disabled="setup.busy || !selectedProject" @click="archiveProject"><Trash2 :size="16" />归档项目</button>
          <p>归档后从项目列表隐藏，历史记录保留。</p>
        </details>
      </aside>
    </section>

    <section v-if="showProjectEditor" class="setup-section compact-step">
      <header>
        <div><h2><Edit3 :size="17" />编辑项目</h2><p>只修改平台侧名称和备注，不会改动 Apifox 原始项目。</p></div>
      </header>
      <div class="scope-picker-grid">
        <label>项目名称<input v-model="projectEditName" data-testid="project-edit-name" placeholder="项目名称" /></label>
        <label>项目备注<input v-model="projectEditDescription" placeholder="例如：3D 家用业务接口" /></label>
        <button data-testid="project-edit-save" class="primary-command scope-check" type="button" :disabled="setup.busy" @click="saveProjectEdit"><Save :size="15" />保存项目</button>
        <button data-testid="project-edit-cancel" class="secondary-command scope-check" type="button" :disabled="setup.busy" @click="cancelProjectEdit">取消</button>
      </div>
    </section>

    <details ref="syncPanel" class="asset-sync-panel" data-testid="apifox-sync-panel" :open="syncOpen" @toggle="syncOpen = ($event.target as HTMLDetailsElement).open">
      <summary>从 Apifox 同步接口 <span>仅更新时需要 · 不会执行接口请求</span></summary>
    <details class="sync-credential" :open="!setup.credential?.configured">
      <summary><KeyRound :size="17" />Apifox 访问令牌 <span>{{ setup.credential?.configured ? '已配置 · 需要更换时展开' : '尚未配置 · 先填写令牌' }}</span></summary>
      <p>只加密保存在平台，不会展示给页面、日志或 AI。</p>
      <div class="credential-row">
        <input v-model="apifoxToken" type="password" autocomplete="off" placeholder="输入新的 Apifox Access Token" @keyup.enter="saveToken" />
        <button class="secondary-command" type="button" :disabled="setup.busy || !apifoxToken.trim()" @click="saveToken"><Save :size="15" />保存令牌</button>

      </div>
    </details>

    <section class="setup-section compact-step">
      <header>
        <div><h2>1. 选择同步来源</h2><p>读取项目 → 选择项目 → 读取环境 → 检查更新。同步来源环境会保存为平台新的执行环境版本。</p></div>
        <button class="primary-command" type="button" :disabled="setup.busy || !setup.credential?.configured" @click="readProjects">
          <RefreshCw v-if="setup.apifoxOperation === 'loading_projects'" class="is-spinning" :size="15" />
          <CloudDownload v-else :size="15" />
          {{ setup.apifoxOperation === 'loading_projects' ? '正在读取项目…' : '读取项目' }}
        </button>
      </header>
      <div class="scope-picker-grid">
        <label>平台项目
          <select v-model="projectId" :disabled="setup.busy" data-testid="platform-project-select" @change="selectProject(projectId)">
            <option value="">请选择</option>
            <option v-for="item in context.projects" :key="item.id" :value="item.id">{{ item.name }}</option>
          </select>
        </label>
        <label>Apifox 项目
          <select v-model="apifoxProjectId" data-testid="apifox-project" :disabled="setup.busy">
            <option value="">{{ setup.apifoxProjects.length ? '请选择' : '先点击读取项目' }}</option>
            <option v-for="item in setup.apifoxProjects" :key="item.id" :value="item.id">{{ item.name }}{{ item.team_name ? ` · ${item.team_name}` : '' }}</option>
          </select>
        </label>
        <button class="secondary-command scope-read" type="button" :disabled="setup.busy || !apifoxProjectId" @click="readContext">
          <RefreshCw :class="{ 'is-spinning': setup.apifoxOperation === 'loading_context' }" :size="15" />
          {{ setup.apifoxOperation === 'loading_context' ? '正在读取环境…' : '读取环境' }}
        </button>
        <label>分支
          <select v-model="branchId" data-testid="apifox-branch" :disabled="setup.busy || !setup.apifoxContext">
            <option v-for="item in setup.apifoxContext?.branches || []" :key="item.id" :value="item.id">{{ apifoxBranchLabel(item) }}</option>
          </select>
        </label>
        <label>Apifox 来源环境
          <select v-model="environmentId" data-testid="apifox-environment" :disabled="setup.busy || !setup.apifoxContext">
            <option value="">请选择</option>
            <option v-for="item in setup.apifoxContext?.environments || []" :key="item.id" :value="item.id">{{ apifoxEnvironmentLabel(item) }}</option>
          </select>
        </label>
        <button class="primary-command scope-check" type="button" :disabled="setup.busy || !canCheckUpdate" @click="checkApifoxUpdate">
          <RefreshCw :class="{ 'is-spinning': setup.apifoxOperation === 'checking_update' }" :size="15" />
          {{ setup.apifoxOperation === 'checking_update' ? '正在检查更新…' : '检查更新' }}
        </button>
      </div>
      <p v-if="selectedEnvironment" class="selection-note">将读取“{{ selectedEnvironment.name }}”的接口定义、服务地址和非敏感变量；只有保存更新后才切换当前测试范围。</p>
      <p v-else class="selection-note">存在多个项目或环境时请明确选择，平台不会默认选中第一个环境。</p>
    </section>

      <section v-if="displayedPreview" class="asset-diff-summary" data-testid="source-preview">
        <h3><Layers :size="16" />{{ setup.preview ? '2. 核对更新，再保存' : '本次更新已保存' }}</h3>
        <div class="diff-review-grid"><div v-for="item in diffSummary" :key="item.label"><strong>{{ item.value }}</strong><span>{{ item.label }}</span></div></div>
        <p v-if="setup.preview">检查更新只生成预览，不会覆盖当前版本。保存后自动切换到新接口版本；Apifox 同步还会切换到“{{ setup.apifoxPreview?.environment_candidate.name || currentEnvironmentRevision?.name || '当前选择' }}”的环境版本。</p>
        <p v-else>当前已切换为接口 v{{ currentRevision?.revision_number }}。点击下方“查看接口”可直接定位新增或变更接口；历史用例仍保留在用例管理。</p>
        <label v-if="displayedPreview.changes.length">查找本次变更<input v-model="changeSearch" data-testid="source-change-search" placeholder="输入接口路径或请求方式" /></label>
        <div class="source-change-list" data-testid="source-changes">
          <table v-if="visibleChanges.length"><thead><tr><th>变更</th><th>接口</th><th>变更内容</th></tr></thead><tbody>
            <tr v-for="(change, index) in visibleChanges" :key="index"><td>{{ changeLabels[String(change.change_type)] || change.change_type }}</td><td><strong>{{ change.method }}</strong> <code>{{ change.path }}</code><RouterLink v-if="savedEndpointLink(change)" class="source-change-link" :to="savedEndpointLink(change)!">查看接口</RouterLink></td><td>{{ changedFields(change) || (change.change_type === 'added' ? '新增接口，可在新版本工作台搜索' : change.change_type === 'removed' ? '旧用例保留，需核对是否仍适用' : '接口定义更新') }}</td></tr>
          </tbody></table>
          <p v-else>{{ changeSearch ? '没有匹配的变更接口' : '接口定义没有变更；仍可保存本次环境更新。' }}</p>
        </div>
        <button v-if="setup.apifoxPreview" class="primary-command" type="button" :disabled="setup.busy" @click="saveApifoxUpdate"><Check :size="16" />{{ setup.apifoxOperation === 'saving_revision' ? '正在保存并切换…' : '保存并切换到新版本' }}</button>
        <button v-else-if="setup.preview" class="primary-command" type="button" :disabled="setup.busy" @click="saveJsonRevision"><Check :size="16" />确认保存接口</button>
      </section>
    </details>

    <details class="advanced-import">
      <summary><FileJson :size="16" />高级导入：接口定义文件（JSON）</summary>
      <div class="advanced-import-body">
        <p>仅在 Apifox 无法读取时使用。读取文件不会立即覆盖当前版本。</p>
        <div class="file-import">
          <label class="file-picker"><input type="file" accept="application/json,.json" @change="pickFile" /><FileJson :size="19" /><span>{{ fileName || '选择 OpenAPI JSON 文件' }}</span></label>
          <button class="secondary-command" type="button" :disabled="setup.busy || !selectedFile" @click="readSource"><RefreshCw :size="15" />读取并比较</button>
        </div>
      </div>
    </details>

    <p v-if="localError || setup.error || assets.error" class="inline-error" role="alert">{{ localError || setup.error || assets.error }}</p>
    <div v-if="setup.message" class="setup-success">
      <Upload :size="16" />
      <span>{{ setup.message }}</span>
      <RouterLink v-if="workbenchLink" :to="workbenchLink"><span>进入工作台</span><ArrowRight :size="14" /></RouterLink>
    </div>
  </section>
</template>
