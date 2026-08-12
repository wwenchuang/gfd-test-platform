<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { Bell, Check, KeyRound, Plus, Save, Trash2 } from 'lucide-vue-next'

import type { EnvironmentView, SourceRevision } from '../api/contracts'
import { apiClient } from '../api/client'
import { useContextStore } from '../stores/context'
import { useNotificationsStore } from '../stores/notifications'
import { type EnvironmentPayload, useSetupStore } from '../stores/setup'

type Pair = { key: string; value: string }
type ServiceRow = { name: string; module: string; base_url: string }

const context = useContextStore()
const setup = useSetupStore()
const notifications = useNotificationsStore()
const projectId = ref('')
const sourceRevisionId = ref('')
const environmentRevisionId = ref('')
const environmentId = ref<string | null>(null)
const name = ref('')
const description = ref('')
const services = ref<ServiceRow[]>([{ name: 'default', module: '默认服务', base_url: '' }])
const variables = ref<Pair[]>([])
const headers = ref<Pair[]>([{ key: 'Authorization', value: 'Bearer {{ZXBToken}}' }])
const secretRows = ref<Array<{ key: string; value: string; configured: boolean }>>([{ key: 'ZXBToken', value: '', configured: false }])
const localError = ref('')
const feishuName = ref('API 基线报告')
const feishuWebhook = ref('')
const feishuEnabled = ref(false)

const sourceOptions = computed(() => context.sourceRevisions.filter(item => item.project_id === projectId.value))
const environmentOptions = computed(() => context.environmentRevisions.filter(item => item.project_id === projectId.value))

onMounted(async () => {
  await Promise.all([context.loadSavedContext(), context.loadOptions()])
  projectId.value = context.projectId || context.projects[0]?.id || ''
  sourceRevisionId.value = context.sourceRevisionId || sourceOptions.value.at(-1)?.id || ''
  environmentRevisionId.value = context.environmentRevisionId || environmentOptions.value.at(-1)?.id || ''
  if (environmentRevisionId.value) await loadEnvironment()
  else await prefillFromSource()
  await loadFeishu()
})

function changeProject(): void {
  sourceRevisionId.value = sourceOptions.value.at(-1)?.id || ''
  environmentRevisionId.value = environmentOptions.value.at(-1)?.id || ''
  environmentId.value = null
  if (environmentRevisionId.value) void loadEnvironment()
  else void prefillFromSource()
  void loadFeishu()
}

async function loadEnvironment(): Promise<void> {
  localError.value = ''
  if (!environmentRevisionId.value) { resetEnvironment(); await prefillFromSource(); return }
  try { applyEnvironment(await setup.loadEnvironmentRevision(environmentRevisionId.value)) }
  catch (error) { localError.value = error instanceof Error ? error.message : '环境读取失败' }
}

async function prefillFromSource(): Promise<void> {
  if (!sourceRevisionId.value) return
  try {
    const response = await apiClient.get<{ source_revision: SourceRevision }>(`/api/api-testing/v1/source-revisions/${sourceRevisionId.value}`)
    const servers = Array.isArray(response.data.source_revision.normalized_document.servers)
      ? response.data.source_revision.normalized_document.servers as Array<Record<string, unknown>> : []
    const url = typeof servers[0]?.url === 'string' ? servers[0].url : ''
    if (url && !services.value.some(item => item.base_url)) services.value = [{ name: 'default', module: '默认服务', base_url: url }]
  } catch {
    // A source without a server declaration remains editable manually.
  }
}

function applyEnvironment(value: EnvironmentView): void {
  environmentId.value = value.id
  name.value = value.name
  description.value = value.description
  services.value = Object.values(value.services).map(item => ({ name: item.name, module: item.module_name || '', base_url: item.base_url || '' }))
  variables.value = []
  secretRows.value = []
  for (const [key, raw] of Object.entries(value.variables)) {
    if (isSecretDescriptor(raw)) secretRows.value.push({ key, value: '', configured: raw.configured === true })
    else variables.value.push({ key, value: displayValue(raw) })
  }
  if (!secretRows.value.length) secretRows.value.push({ key: 'ZXBToken', value: '', configured: false })
  headers.value = Object.entries(value.default_headers).map(([key, raw]) => ({ key, value: String(raw ?? '') }))
}

function resetEnvironment(): void {
  environmentId.value = null
  name.value = ''
  description.value = ''
  services.value = [{ name: 'default', module: '默认服务', base_url: '' }]
  variables.value = []
  headers.value = [{ key: 'Authorization', value: 'Bearer {{ZXBToken}}' }]
  secretRows.value = [{ key: 'ZXBToken', value: '', configured: false }]
}

async function save(): Promise<void> {
  localError.value = ''
  if (!projectId.value || !name.value.trim()) { localError.value = '请选择项目并填写环境名称'; return }
  const usableServices = services.value.filter(item => item.name.trim())
  if (!usableServices.length || usableServices.every(item => !item.base_url.trim())) { localError.value = '至少配置一个可用服务地址'; return }
  setup.secretUpdates = Object.fromEntries(secretRows.value.filter(item => item.key.trim() && item.value).map(item => [item.key.trim(), item.value]))
  const payload: EnvironmentPayload = {
    project_id: projectId.value,
    source_id: sourceOptions.value.find(item => item.id === sourceRevisionId.value)?.source_id || null,
    source_revision_id: sourceRevisionId.value || null,
    name: name.value.trim(), description: description.value,
    services: usableServices.map(item => ({ name: item.name.trim(), module: item.module.trim() || '默认模块', base_url: item.base_url.trim() || null })),
    variables: objectFromPairs(variables.value), default_headers: objectFromPairs(headers.value) as Record<string, string>,
  }
  try {
    const saved = await setup.saveEnvironment(environmentId.value, payload)
    secretRows.value.forEach(item => { item.value = ''; item.configured = true })
    await context.loadOptions()
    environmentRevisionId.value = saved.revision_id
    environmentId.value = saved.id
  } catch (error) { localError.value = error instanceof Error ? error.message : '环境保存失败' }
}

async function loadFeishu(): Promise<void> {
  if (!projectId.value) return
  await notifications.loadFeishu(projectId.value)
  const current = notifications.feishu
  if (!current) return
  feishuName.value = current.name || 'API 基线报告'
  feishuEnabled.value = current.enabled
  feishuWebhook.value = ''
}

async function saveFeishu(): Promise<void> {
  localError.value = ''
  if (!projectId.value) { localError.value = '请先选择项目'; return }
  try {
    await notifications.saveFeishu(projectId.value, {
      name: feishuName.value.trim() || 'API 基线报告',
      enabled: feishuEnabled.value,
      webhook: feishuWebhook.value.trim(),
    })
    feishuWebhook.value = ''
  } catch (error) {
    localError.value = error instanceof Error ? error.message : '飞书通知保存失败'
  }
}

function addPair(rows: Pair[]): void { rows.push({ key: '', value: '' }) }
function objectFromPairs(rows: Pair[]): Record<string, unknown> { return Object.fromEntries(rows.filter(item => item.key.trim()).map(item => [item.key.trim(), parseValue(item.value)])) }
function parseValue(value: string): unknown { try { return JSON.parse(value) } catch { return value } }
function displayValue(value: unknown): string { return typeof value === 'string' ? value : JSON.stringify(value) }
function isSecretDescriptor(value: unknown): value is { configured: boolean } { return Boolean(value && typeof value === 'object' && 'configured' in value) }
</script>

<template>
  <section class="workspace setup-page">
    <header class="page-toolbar"><div><p class="eyebrow">API ENVIRONMENT</p><h1>环境配置</h1><p class="page-subtitle">服务地址、公共变量和业务 token 在这里统一管理，执行时按变量名注入。</p></div><button class="primary-command" type="button" :disabled="setup.busy" @click="save"><Save :size="15" />{{ setup.busy ? '正在保存' : '保存环境' }}</button></header>
    <section class="setup-section"><header><div><h2>选择范围</h2><p>已有环境直接编辑；选择“新建环境”不会覆盖历史版本。</p></div></header><div class="setup-grid three"><label>项目<select v-model="projectId" @change="changeProject"><option value="">请选择项目</option><option v-for="item in context.projects" :key="item.id" :value="item.id">{{ item.name }}</option></select></label><label>接口版本<select v-model="sourceRevisionId" @change="prefillFromSource"><option value="">不绑定接口版本</option><option v-for="item in sourceOptions" :key="item.id" :value="item.id">{{ item.name }} · v{{ item.revision_number }}</option></select></label><label>环境<select v-model="environmentRevisionId" @change="loadEnvironment"><option value="">新建环境</option><option v-for="item in environmentOptions" :key="item.id" :value="item.id">{{ item.name }} · v{{ item.revision }}</option></select></label></div></section>
    <section class="setup-section"><header><div><h2>基本信息</h2><p>工作台和报告只展示名称，不展示数据库 ID。</p></div></header><div class="setup-grid"><label>环境名称<input v-model="name" placeholder="例如：生产环境（新）- 腾讯云" /></label><label>说明<input v-model="description" placeholder="可选" /></label></div></section>
    <section class="setup-section"><header><div><h2>服务地址</h2><p>用例通过服务名选择 Base URL。</p></div><button class="mini-icon" type="button" title="添加服务" @click="services.push({ name: '', module: '', base_url: '' })"><Plus :size="15" /></button></header><div class="editable-table"><div class="table-head"><span>服务名</span><span>模块</span><span>Base URL</span><span></span></div><div v-for="(item, index) in services" :key="index" class="table-row service-row"><input v-model="item.name" aria-label="服务名" /><input v-model="item.module" aria-label="服务模块" /><input v-model="item.base_url" aria-label="服务地址" placeholder="https://api.example.com" /><button class="mini-icon danger" type="button" title="删除服务" @click="services.splice(index, 1)"><Trash2 :size="14" /></button></div></div></section>
    <section class="setup-section split-section"><div><header><div><h2>公共变量</h2><p>普通值可回显和编辑。</p></div><button class="mini-icon" type="button" title="添加变量" @click="addPair(variables)"><Plus :size="15" /></button></header><div class="pair-list"><div v-for="(item, index) in variables" :key="index" class="pair-row"><input v-model="item.key" aria-label="变量名" placeholder="变量名" /><input v-model="item.value" aria-label="变量值" placeholder="变量值" /><button class="mini-icon danger" type="button" title="删除变量" @click="variables.splice(index, 1)"><Trash2 :size="14" /></button></div><p v-if="!variables.length" class="compact-empty">暂无公共变量</p></div></div><div><header><div><h2>敏感变量</h2><p>只显示是否已配置，新值提交后立即清空。</p></div><button class="mini-icon" type="button" title="添加敏感变量" @click="secretRows.push({ key: '', value: '', configured: false })"><Plus :size="15" /></button></header><div class="pair-list"><div v-for="(item, index) in secretRows" :key="index" class="pair-row secret-row"><input v-model="item.key" aria-label="敏感变量名" placeholder="例如 ZXBToken" /><input v-model="item.value" type="password" autocomplete="new-password" aria-label="敏感变量值" :placeholder="item.configured ? '已配置，留空则保持不变' : '输入后仅发送一次'" /><KeyRound :size="15" :class="item.configured ? 'secret-configured' : 'secret-empty'" /></div></div></div></section>
    <section class="setup-section"><header><div><h2>默认请求头</h2><p>推荐引用变量，例如 Authorization: Bearer &#123;&#123;ZXBToken&#125;&#125;。</p></div><button class="mini-icon" type="button" title="添加请求头" @click="addPair(headers)"><Plus :size="15" /></button></header><div class="pair-list"><div v-for="(item, index) in headers" :key="index" class="pair-row"><input v-model="item.key" aria-label="请求头名称" /><input v-model="item.value" aria-label="请求头值" /><button class="mini-icon danger" type="button" title="删除请求头" @click="headers.splice(index, 1)"><Trash2 :size="14" /></button></div></div></section>
    <section class="setup-section notification-section">
      <header>
        <div><h2>飞书报告通知</h2><p>基线回归报告可一键发送到飞书群。Webhook 加密保存，留空表示保留原值。</p></div>
        <span v-if="notifications.feishu?.configured" class="configured-state"><Check :size="14" />已配置 {{ notifications.feishu.fingerprint }}</span>
      </header>
      <div class="setup-grid three">
        <label>通知名称<input v-model="feishuName" placeholder="例如：API 基线报告" /></label>
        <label class="grow">飞书群机器人 Webhook<input v-model="feishuWebhook" type="password" autocomplete="new-password" placeholder="https://open.feishu.cn/open-apis/bot/v2/hook/..." /></label>
        <label class="toggle-card"><input v-model="feishuEnabled" type="checkbox" />启用报告发送</label>
      </div>
      <footer class="notification-actions">
        <span><Bell :size="14" />报告页会按当前项目发送最近执行结果；未启用时不会误发。</span>
        <button class="secondary-command" type="button" :disabled="notifications.loading || !projectId" @click="loadFeishu">{{ notifications.loading ? '读取中' : '读取配置' }}</button>
        <button class="primary-command" type="button" :disabled="notifications.saving || !projectId" @click="saveFeishu">{{ notifications.saving ? '保存中' : '保存飞书配置' }}</button>
      </footer>
    </section>
    <p v-if="localError || setup.error || notifications.error" class="inline-error" role="alert">{{ localError || setup.error || notifications.error }}</p><p v-if="setup.message" class="setup-success"><Check :size="16" />{{ setup.message }}</p><p v-if="notifications.message" class="setup-success"><Bell :size="16" />{{ notifications.message }}</p>
  </section>
</template>
