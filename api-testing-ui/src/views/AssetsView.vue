<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import {
  ArrowRight, Check, CheckCircle2, CloudDownload, FileJson, FolderPlus,
  KeyRound, RefreshCw, Save, Upload,
} from 'lucide-vue-next'

import { useContextStore } from '../stores/context'
import { useSetupStore } from '../stores/setup'

const context = useContextStore()
const setup = useSetupStore()
const projectId = ref('')
const revisionId = ref('')
const projectName = ref('')
const showProjectForm = ref(false)
const apifoxToken = ref('')
const apifoxProjectId = ref('')
const branchId = ref('')
const environmentId = ref('')
const selectedFile = ref<File | null>(null)
const fileName = ref('')
const localError = ref('')

const revisions = computed(() => context.sourceRevisions.filter(item => item.project_id === projectId.value))
const currentRevision = computed(() => revisions.value.find(item => item.id === revisionId.value) || null)
const selectedEnvironment = computed(() => setup.apifoxContext?.environments.find(item => item.id === environmentId.value) || null)
const canCheckUpdate = computed(() => Boolean(projectId.value && apifoxProjectId.value && environmentId.value))

onMounted(async () => {
  await Promise.all([context.loadSavedContext(), context.loadOptions(), setup.loadApifoxCredential()])
  projectId.value = context.projectId || context.projects[0]?.id || ''
  chooseDefaultRevision()
})

function chooseDefaultRevision(): void {
  const options = context.sourceRevisions.filter(item => item.project_id === projectId.value)
  revisionId.value = options.some(item => item.id === context.sourceRevisionId)
    ? context.sourceRevisionId || '' : options.at(-1)?.id || ''
  setup.apifoxPreview = null
  setup.preview = null
}

async function createProject(): Promise<void> {
  localError.value = ''
  if (!projectName.value.trim()) { localError.value = '请输入项目名称'; return }
  try {
    projectId.value = await setup.createProject(projectName.value)
    await context.loadOptions()
    projectName.value = ''
    showProjectForm.value = false
    revisionId.value = ''
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
    apifoxProjectId.value = projects[0]?.id || ''
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
    environmentId.value = result.environments[0]?.id || ''
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
  try {
    const result = await setup.activateApifoxPreview()
    context.applyWorkspace(result.workspace)
    await context.loadOptions()
    revisionId.value = result.source_revision.id
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
  } catch (error) {
    localError.value = error instanceof SyntaxError ? '接口文件不是有效的 JSON' : error instanceof Error ? error.message : '接口文件读取失败'
  }
}

async function saveJsonRevision(): Promise<void> {
  localError.value = ''
  try {
    const revision = await setup.activatePreview()
    await context.loadOptions()
    revisionId.value = revision.id
    selectedFile.value = null
    fileName.value = ''
  } catch (error) { localError.value = error instanceof Error ? error.message : '接口版本保存失败' }
}
</script>

<template>
  <section class="workspace setup-page">
    <header class="page-toolbar"><div><p class="eyebrow">API SOURCE</p><h1>接口资产</h1><p class="page-subtitle">已保存的接口可直接测试；只有你点击读取时，平台才访问 Apifox。</p></div></header>

    <section class="saved-asset-bar">
      <div><span>平台项目</span><strong>{{ context.projects.find(item => item.id === projectId)?.name || '尚未选择' }}</strong></div>
      <div><span>当前接口</span><strong>{{ currentRevision ? `v${currentRevision.revision_number} · ${currentRevision.endpoint_count} 个接口` : '尚未保存' }}</strong></div>
      <RouterLink v-if="currentRevision" to="/"><span>直接开始测试</span><ArrowRight :size="14" /></RouterLink>
    </section>

    <ol class="setup-steps" aria-label="Apifox 更新步骤"><li :class="{ active: !setup.credential?.configured }"><span>1</span>保存访问令牌</li><li :class="{ active: setup.credential?.configured && !setup.apifoxContext }"><span>2</span>选择项目与环境</li><li :class="{ active: setup.apifoxPreview }"><span>3</span>检查变化并保存</li></ol>

    <section class="setup-section compact-step">
      <header><div><h2><KeyRound :size="17" />访问令牌</h2><p>只加密保存在平台，不会展示给页面、日志或 AI。</p></div><span v-if="setup.credential?.configured" class="configured-state"><CheckCircle2 :size="15" />已配置 · {{ setup.credential.fingerprint }}</span></header>
      <div class="credential-row"><input v-model="apifoxToken" type="password" autocomplete="off" placeholder="输入新的 Apifox Access Token" @keyup.enter="saveToken" /><button class="secondary-command" type="button" :disabled="setup.busy || !apifoxToken.trim()" @click="saveToken"><Save :size="15" />保存令牌</button><button class="primary-command" type="button" :disabled="setup.busy || !setup.credential?.configured" @click="readProjects"><CloudDownload :size="15" />读取项目</button></div>
    </section>

    <section class="setup-section compact-step">
      <header><div><h2>测试范围</h2><p>选择保存到哪个平台项目，以及这次从 Apifox 读取哪个环境。</p></div><button class="secondary-command" type="button" @click="showProjectForm = !showProjectForm"><FolderPlus :size="15" />新建平台项目</button></header>
      <div v-if="showProjectForm" class="inline-create"><label>项目名称<input v-model="projectName" placeholder="例如：3D 家用业务" /></label><button class="primary-command" type="button" @click="createProject">创建</button></div>
      <div class="scope-picker-grid">
        <label>平台项目<select v-model="projectId" @change="chooseDefaultRevision"><option value="">请选择</option><option v-for="item in context.projects" :key="item.id" :value="item.id">{{ item.name }}</option></select></label>
        <label>Apifox 项目<select v-model="apifoxProjectId"><option value="">{{ setup.apifoxProjects.length ? '请选择' : '先点击读取项目' }}</option><option v-for="item in setup.apifoxProjects" :key="item.id" :value="item.id">{{ item.name }}{{ item.team_name ? ` · ${item.team_name}` : '' }}</option></select></label>
        <button class="secondary-command scope-read" type="button" :disabled="setup.busy || !apifoxProjectId" @click="readContext"><RefreshCw :size="15" />读取环境</button>
        <label>分支<select v-model="branchId" :disabled="!setup.apifoxContext"><option v-for="item in setup.apifoxContext?.branches || []" :key="item.id" :value="item.id">{{ item.name }}</option></select></label>
        <label>执行环境<select v-model="environmentId" :disabled="!setup.apifoxContext"><option value="">请选择</option><option v-for="item in setup.apifoxContext?.environments || []" :key="item.id" :value="item.id">{{ item.name }}</option></select></label>
        <button class="primary-command scope-check" type="button" :disabled="setup.busy || !canCheckUpdate" @click="checkApifoxUpdate"><RefreshCw :size="15" />检查更新</button>
      </div>
      <p v-if="selectedEnvironment" class="selection-note">将读取“{{ selectedEnvironment.name }}”的接口定义、服务地址和非敏感变量。</p>
    </section>

    <section v-if="setup.apifoxPreview" class="setup-section diff-review">
      <header><div><h2>变化确认</h2><p>当前版本尚未改变。确认后，接口和环境会一起保存为新版本。</p></div><button class="primary-command" type="button" :disabled="setup.busy" @click="saveApifoxUpdate"><Check :size="15" />确认保存</button></header>
      <div class="diff-review-grid"><div class="diff-counts"><div><strong>{{ setup.preview?.added_count }}</strong><span>新增</span></div><div><strong>{{ setup.preview?.changed_count }}</strong><span>变更</span></div><div><strong>{{ setup.preview?.removed_count }}</strong><span>删除</span></div></div><div class="environment-preview"><span>环境</span><strong>{{ setup.apifoxPreview.environment_candidate.name }}</strong><small v-if="setup.secretPlaceholders.length">保存后需在环境配置中填写：{{ setup.secretPlaceholders.join('、') }}</small><small v-else>未发现待配置的敏感变量</small></div></div>
    </section>

    <details class="advanced-import"><summary><FileJson :size="16" />高级导入：OpenAPI JSON</summary><div class="advanced-import-body"><p>仅在 Apifox 无法读取时使用。读取文件不会立即覆盖当前版本。</p><div class="file-import"><label class="file-picker"><input type="file" accept="application/json,.json" @change="pickFile" /><FileJson :size="19" /><span>{{ fileName || '选择 OpenAPI JSON 文件' }}</span></label><button class="secondary-command" type="button" :disabled="setup.busy || !selectedFile" @click="readSource"><RefreshCw :size="15" />读取并比较</button><button v-if="setup.preview && !setup.apifoxPreview" class="primary-command" type="button" :disabled="setup.busy" @click="saveJsonRevision"><Check :size="15" />确认保存接口</button></div></div></details>

    <p v-if="localError || setup.error" class="inline-error" role="alert">{{ localError || setup.error }}</p>
    <div v-if="setup.message" class="setup-success"><Upload :size="16" /><span>{{ setup.message }}</span><RouterLink v-if="setup.activeRevision" to="/"><span>进入工作台</span><ArrowRight :size="14" /></RouterLink></div>
  </section>
</template>
