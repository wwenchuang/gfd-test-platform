<script setup lang="ts">
import { computed, ref } from 'vue'
import type { ApiEndpoint, LoadScenarioDefinition } from '../api/contracts'

defineOptions({ name: 'LoadScenarioWizard' })
const props = defineProps<{ endpoints: ApiEndpoint[]; initialDefinition?: LoadScenarioDefinition | null; projectName?: string }>()
const emit = defineEmits<{ save: [definition: LoadScenarioDefinition]; cancel: [] }>()
const step = ref(1)
const name = ref(props.initialDefinition?.name || '')
const description = ref(props.initialDefinition?.description || '')
const mode = ref<'single_interface' | 'workflow'>(props.initialDefinition?.mode || 'single_interface')
const sourceItems = Array.isArray(props.initialDefinition?.source_snapshot?.items) ? props.initialDefinition?.source_snapshot.items as Array<{ id?: string }> : []
const selectedIds = ref<string[]>(sourceItems.flatMap(item => item.id ? [String(item.id)] : []))
const datasetMode = ref<'cycle' | 'fixed_per_vu' | 'exclusive_per_iteration'>(props.initialDefinition?.dataset_contract?.usage_mode || 'cycle')
const query = ref('')

const selected = computed(() => selectedIds.value.map(id => props.endpoints.find(item => item.id === id)).filter((item): item is ApiEndpoint => Boolean(item)))
const filtered = computed(() => {
  const value = query.value.trim().toLocaleLowerCase('zh-CN')
  return value ? props.endpoints.filter(item => `${item.method} ${item.path} ${item.summary}`.toLocaleLowerCase('zh-CN').includes(value)) : props.endpoints
})
const warning = computed(() => selected.value.some(item => !['GET', 'HEAD', 'OPTIONS'].includes(item.method.toUpperCase())))
const canNext = computed(() => step.value > 1 || Boolean(name.value.trim() && selected.value.length))

function choose(endpoint: ApiEndpoint): void {
  if (mode.value === 'single_interface') selectedIds.value = [endpoint.id]
  else selectedIds.value = selectedIds.value.includes(endpoint.id) ? selectedIds.value.filter(id => id !== endpoint.id) : [...selectedIds.value, endpoint.id]
}
function setMode(value: 'single_interface' | 'workflow'): void { mode.value = value; if (value === 'single_interface') selectedIds.value = selectedIds.value.slice(0, 1) }
function save(): void {
  const definition: LoadScenarioDefinition = {
    name: name.value.trim(), description: description.value.trim(), mode: mode.value,
    steps: selected.value.map((endpoint, index) => ({
      id: `step-${index + 1}`, name: endpoint.summary || endpoint.path, scope: 'iteration', action: 'http_request',
      request: { method: endpoint.method.toUpperCase(), path: endpoint.path, service: 'default', path_params: {}, query: {}, headers: {}, cookies: {}, body: null },
      assertions: [{ type: 'status_code', operator: 'equals', expected: 200, enabled: true }], extractions: [], sleep_ms: 0,
      side_effect: ['GET', 'HEAD', 'OPTIONS'].includes(endpoint.method.toUpperCase()) ? 'readonly' : 'creates_owned_resource',
    })),
    dataset_contract: { dataset_id: null, usage_mode: datasetMode.value, variables: [] },
    risk: { level: warning.value ? 'high' : 'low', ownership_variable: null, notes: warning.value ? '写操作需补充归属变量和清理步骤' : '' },
    source_snapshot: { type: 'endpoint', version_ids: [], items: selected.value.map(item => ({ id: item.id, name: item.summary, tags: item.tags, request: { method: item.method, path: item.path } })) },
  }
  emit('save', definition)
}
</script>

<template>
  <section class="load-wizard" aria-label="创建性能场景">
    <header><div><p class="eyebrow">第 {{ step }} 步，共 3 步</p><h2>{{ initialDefinition ? '创建新版本' : '创建性能场景' }}</h2></div><button data-testid="scenario-cancel" class="text-command" type="button" @click="emit('cancel')">← 返回场景列表</button></header>
    <ol class="load-stepper"><li :class="{ active: step === 1 }">1 选择接口</li><li :class="{ active: step === 2 }">2 数据与断言</li><li :class="{ active: step === 3 }">3 确认保存</li></ol>
    <div v-if="step === 1" class="load-wizard-body">
      <section class="load-context-banner"><div><span>所属应用 / API 项目</span><strong>{{ projectName || '当前接口项目' }}</strong><small>场景只使用该项目已同步的接口；需要换应用时请先回工作台切换。</small></div><div><span>版本策略</span><strong>{{ initialDefinition ? '保留旧版本并新建版本' : '首次创建' }}</strong><small>历史执行继续引用原版本，不会被本次编辑覆盖。</small></div></section>
      <label>场景名称<input v-model="name" data-testid="load-scenario-name" placeholder="例如：模型搜索容量验证" /></label>
      <label>场景说明<textarea v-model="description" rows="2" placeholder="说明要验证的业务目标" /></label>
      <div class="load-option-grid"><button data-testid="scenario-mode-single" :class="{ active: mode === 'single_interface' }" type="button" @click="setMode('single_interface')"><strong>单接口压测</strong><span>每轮只请求一个接口，适合测接口容量。</span></button><button data-testid="scenario-mode-workflow" :class="{ active: mode === 'workflow' }" type="button" @click="setMode('workflow')"><strong>业务链路压测</strong><span>按顺序执行多个接口，吞吐指完整链路次数。</span></button></div>
      <label class="search-box"><span class="sr-only">搜索接口</span><input v-model="query" type="search" placeholder="搜索接口名称或路径" /></label>
      <div class="load-endpoint-list"><button v-for="endpoint in filtered" :key="endpoint.id" :data-testid="`scenario-endpoint-${endpoint.id}`" :class="{ active: selectedIds.includes(endpoint.id) }" type="button" @click="choose(endpoint)"><b>{{ endpoint.method }}</b><span><strong>{{ endpoint.summary || endpoint.path }}</strong><code>{{ endpoint.path }}</code></span></button></div>
      <p v-if="warning" class="load-warning">写接口必须说明资源归属并配置清理步骤；当前草稿会交由服务端校验，未补齐时不能保存版本。</p>
    </div>
    <div v-else-if="step === 2" class="load-wizard-body">
      <h3>数据取用方式</h3>
      <div class="load-option-grid three"><button v-for="option in [{ value: 'cycle', label: '循环共享', help: '只读查询优先；数据用完后重复使用。' }, { value: 'fixed_per_vu', label: '每个用户固定一行', help: '适合固定账号，数据不足时会复用。' }, { value: 'exclusive_per_iteration', label: '每次迭代独占一行', help: '适合一次性数据，耗尽后停止。' }]" :key="option.value" :class="{ active: datasetMode === option.value }" type="button" @click="datasetMode = option.value as typeof datasetMode"><strong>{{ option.label }}</strong><span>{{ option.help }}</span></button></div>
      <div class="load-review-box"><strong>默认业务校验</strong><p>每个接口先校验 HTTP 状态 200。HTTP 200 不代表业务成功；保存后应按真实响应补充业务码、布尔值或领域字段断言。</p></div>
    </div>
    <div v-else class="load-wizard-body"><h3>保存前确认</h3><div class="load-review-box"><strong>{{ name }}</strong><p>{{ mode === 'single_interface' ? '单接口压测' : '业务链路压测' }} · {{ selected.length }} 个接口 · {{ datasetMode }}</p><p v-for="item in selected" :key="item.id">{{ item.method }} {{ item.path }}</p></div><p>保存会创建不可变场景版本，并由服务端再次执行安全校验。校验失败会保留问题和处理办法，不会进入压测。</p></div>
    <footer><button v-if="step > 1" data-testid="scenario-back" class="secondary-command" type="button" @click="step--">上一步</button><span /><button v-if="step < 3" data-testid="scenario-next" class="primary-command" type="button" :disabled="!canNext" @click="step++">下一步</button><button v-else data-testid="scenario-save" class="primary-command" type="button" :disabled="warning" :title="warning ? '写操作必须先配置资源归属与清理步骤' : ''" @click="save">保存并校验版本</button></footer>
  </section>
</template>
