<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { ArrowRight, Check, FileJson, FolderPlus, RefreshCw, Upload } from 'lucide-vue-next'

import { useContextStore } from '../stores/context'
import { useSetupStore } from '../stores/setup'

const context = useContextStore()
const setup = useSetupStore()
const projectId = ref('')
const revisionId = ref('')
const projectName = ref('')
const showProjectForm = ref(false)
const selectedFile = ref<File | null>(null)
const fileName = ref('')
const localError = ref('')

const revisions = computed(() => context.sourceRevisions.filter(item => item.project_id === projectId.value))
const currentRevision = computed(() => revisions.value.find(item => item.id === revisionId.value) || null)

onMounted(async () => {
  await Promise.all([context.loadSavedContext(), context.loadOptions()])
  projectId.value = context.projectId || context.projects[0]?.id || ''
  chooseDefaultRevision()
})

function chooseDefaultRevision(): void {
  const options = context.sourceRevisions.filter(item => item.project_id === projectId.value)
  revisionId.value = options.some(item => item.id === context.sourceRevisionId)
    ? context.sourceRevisionId || '' : options.at(-1)?.id || ''
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

function pickFile(event: Event): void {
  const file = (event.target as HTMLInputElement).files?.[0] || null
  selectedFile.value = file
  fileName.value = file?.name || ''
  setup.preview = null
  setup.message = ''
}

async function readSource(): Promise<void> {
  localError.value = ''
  if (!projectId.value) { localError.value = '请先选择项目'; return }
  if (!selectedFile.value) { localError.value = '请选择 Apifox 导出的 OpenAPI JSON 文件'; return }
  try {
    const document = JSON.parse(await selectedFile.value.text()) as Record<string, unknown>
    await setup.previewSource(projectId.value, currentRevision.value?.source_id || null, document)
  } catch (error) {
    localError.value = error instanceof SyntaxError ? '接口文件不是有效的 JSON' : error instanceof Error ? error.message : '接口文件读取失败'
  }
}

async function saveRevision(): Promise<void> {
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
    <header class="page-toolbar"><div><p class="eyebrow">API SOURCE</p><h1>接口资产</h1><p class="page-subtitle">只在需要更新时手动读取 Apifox 导出的 OpenAPI 文件，确认变化后保存版本。</p></div></header>
    <ol class="setup-steps" aria-label="接口导入步骤"><li class="active"><span>1</span>选择项目</li><li :class="{ active: selectedFile }"><span>2</span>读取接口</li><li :class="{ active: setup.preview }"><span>3</span>确认保存</li></ol>
    <section class="setup-section">
      <header><div><h2>接口项目</h2><p>同事进入后直接使用已保存版本，不会自动访问 Apifox。</p></div><button class="secondary-command" type="button" @click="showProjectForm = !showProjectForm"><FolderPlus :size="15" />新建项目</button></header>
      <div v-if="showProjectForm" class="inline-create"><label>项目名称<input v-model="projectName" placeholder="例如：3D 家用业务" /></label><button class="primary-command" type="button" @click="createProject">创建</button></div>
      <div class="setup-grid"><label>项目<select v-model="projectId" @change="chooseDefaultRevision"><option value="">请选择项目</option><option v-for="item in context.projects" :key="item.id" :value="item.id">{{ item.name }}</option></select></label><label>当前已保存版本<select v-model="revisionId"><option value="">尚未保存接口</option><option v-for="item in revisions" :key="item.id" :value="item.id">{{ item.name }} · v{{ item.revision_number }} · {{ item.endpoint_count }} 个接口</option></select></label></div>
    </section>
    <section class="setup-section">
      <header><div><h2>手动更新接口</h2><p>在 Apifox 导出 OpenAPI 3.0 JSON，再在这里读取。读取不会立即覆盖当前版本。</p></div></header>
      <div class="file-import"><label class="file-picker"><input type="file" accept="application/json,.json" @change="pickFile" /><FileJson :size="19" /><span>{{ fileName || '选择 OpenAPI JSON 文件' }}</span></label><button class="primary-command" type="button" :disabled="setup.busy || !selectedFile" @click="readSource"><RefreshCw :size="15" />{{ setup.busy ? '正在读取' : '读取并比较' }}</button></div>
    </section>
    <section v-if="setup.preview" class="setup-section diff-review">
      <header><div><h2>变化确认</h2><p>保存后生成新的只读接口版本，已有用例和执行记录不被改写。</p></div><button class="primary-command" type="button" :disabled="setup.busy" @click="saveRevision"><Check :size="15" />确认保存</button></header>
      <div class="diff-counts"><div><strong>{{ setup.preview.added_count }}</strong><span>新增</span></div><div><strong>{{ setup.preview.changed_count }}</strong><span>变更</span></div><div><strong>{{ setup.preview.removed_count }}</strong><span>删除</span></div></div>
    </section>
    <p v-if="localError || setup.error" class="inline-error" role="alert">{{ localError || setup.error }}</p>
    <div v-if="setup.message" class="setup-success"><Upload :size="16" /><span>{{ setup.message }}</span><RouterLink v-if="setup.activeRevision" to="/"><span>进入工作台</span><ArrowRight :size="14" /></RouterLink></div>
  </section>
</template>
