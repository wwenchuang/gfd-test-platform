<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import type { LoadScenarioDefinition } from '../api/contracts'
import { apiClient } from '../api/client'

const props = defineProps<{ modelValue: LoadScenarioDefinition; projectId?: string }>()
const emit = defineEmits<{ 'update:modelValue': [value: LoadScenarioDefinition]; pending: [value: boolean] }>()
const draft = ref(JSON.stringify(props.modelValue, null, 2))
const dirty = ref(false)
const error = ref('')
interface DatasetMetadata { id: string; name: string; row_count: number; field_schema?: { fields?: Array<{ name: string }> }; fields?: string[] }
const datasets = ref<DatasetMetadata[]>([])
const datasetError = ref('')
const datasetBusy = ref(false)
const file = ref<File | null>(null)
const datasetName = ref('')
const selectedDataset = computed(() => datasets.value.find(item => item.id === props.modelValue.dataset_contract.dataset_id))
const datasetFields = computed(() => selectedDataset.value?.field_schema?.fields?.map(item => item.name) || selectedDataset.value?.fields || [])
watch(() => props.projectId, () => { datasets.value = []; void loadDatasets() }, { immediate: true })
watch(() => props.modelValue, value => { if (!dirty.value) draft.value = JSON.stringify(value, null, 2) }, { deep: true })
function edit(): void { dirty.value = true; error.value = ''; emit('pending', true) }
function apply(): void {
  let value: LoadScenarioDefinition
  try { value = JSON.parse(draft.value) }
  catch { error.value = 'JSON 格式无效，请检查引号、逗号和括号；当前输入已保留。'; return }
  if (!value || typeof value !== 'object' || !Array.isArray(value.steps) || !value.steps.length ||
      typeof value.name !== 'string' || !value.name.trim() || typeof value.description !== 'string' ||
      !['single_interface', 'workflow'].includes(value.mode) || !value.dataset_contract || !value.risk || !value.source_snapshot ||
      !Array.isArray(value.dataset_contract.variables) || !['cycle', 'fixed_per_vu', 'exclusive_per_iteration'].includes(value.dataset_contract.usage_mode) ||
      value.steps.some(item => !item || typeof item !== 'object' || typeof item.id !== 'string' || typeof item.name !== 'string' ||
        typeof item.action !== 'string' || !Array.isArray(item.assertions) || !Array.isArray(item.extractions))) {
    error.value = '请保留场景名称、模式、步骤、数据契约、风险和来源配置；步骤至少一条。'; return
  }
  dirty.value = false; error.value = ''; emit('update:modelValue', value); emit('pending', false)
}
async function loadDatasets(): Promise<void> {
  if (!props.projectId) return
  const projectId = props.projectId
  datasetError.value = ''
  try {
    const response = await apiClient.get<{ datasets: DatasetMetadata[] }>(`/api/api-testing/v1/load-datasets?project_id=${encodeURIComponent(projectId)}`)
    if (props.projectId === projectId) datasets.value = response.data.datasets
  } catch { datasetError.value = '读取数据集失败，请刷新重试。原有数据集绑定仍保留。' }
}
function bindDataset(id: string): void {
  if (dirty.value) return
  emit('update:modelValue', { ...props.modelValue, dataset_contract: { ...props.modelValue.dataset_contract, dataset_id: id || null } })
}
function pickFile(event: Event): void {
  file.value = (event.target as HTMLInputElement).files?.[0] || null
  datasetName.value = file.value?.name.replace(/\.(csv|json)$/i, '') || ''
  datasetError.value = ''
}
function base64Content(value: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader()
    reader.onload = () => resolve(String(reader.result).split(',', 2)[1] || '')
    reader.onerror = () => reject(new Error('文件读取失败'))
    reader.readAsDataURL(value)
  })
}
async function importDataset(): Promise<void> {
  if (!file.value || !props.projectId || datasetBusy.value || dirty.value) return
  datasetError.value = ''
  if (!/\.(csv|json)$/i.test(file.value.name)) { datasetError.value = '请选择 UTF-8 编码的 CSV 或 JSON 文件。'; return }
  if (file.value.size > 700 * 1024) { datasetError.value = '网页导入目前支持不超过 700 KB 的文件，请拆分后导入。'; return }
  if (!datasetName.value.trim()) { datasetError.value = '请填写数据集名称。'; return }
  datasetBusy.value = true
  try {
    const response = await apiClient.post<{ dataset: DatasetMetadata }>('/api/api-testing/v1/load-datasets', {
      project_id: props.projectId, name: datasetName.value.trim(), filename: file.value.name,
      content_base64: await base64Content(file.value), usage_mode: props.modelValue.dataset_contract.usage_mode,
    })
    const created = response.data.dataset
    // Keep metadata only; import response previews may contain business data.
    datasets.value = [{ id: created.id, name: created.name, row_count: created.row_count, fields: created.fields }, ...datasets.value]
    bindDataset(created.id)
    file.value = null
  } catch { datasetError.value = '数据集导入失败，请检查文件格式、列名及访问权限后重试。当前场景配置已保留。' }
  finally { datasetBusy.value = false }
}
</script>

<template>
  <section class="scenario-advanced" aria-label="场景完整配置">
    <section v-if="projectId" class="dataset-config" aria-label="数据集绑定与导入">
      <header><h3>数据集</h3><p>选择本项目已有数据，或导入 CSV / JSON。这里只显示行数和列名，字段绑定在下方完整配置中声明。</p></header>
      <label>绑定数据集<select :value="modelValue.dataset_contract.dataset_id || ''" data-testid="scenario-dataset-select" :disabled="dirty || datasetBusy" @change="bindDataset(($event.target as HTMLSelectElement).value)"><option value="">暂不绑定数据集</option><option v-if="modelValue.dataset_contract.dataset_id && !selectedDataset" :value="modelValue.dataset_contract.dataset_id">保留原绑定：{{ modelValue.dataset_contract.dataset_id }}</option><option v-for="item in datasets" :key="item.id" :value="item.id">{{ item.name }} · {{ item.row_count }} 行</option></select></label>
      <p v-if="datasetFields.length">可用字段：{{ datasetFields.join('、') }}</p>
      <button class="text-command" type="button" @click="loadDatasets">刷新数据集</button>
      <details><summary>导入新数据集</summary><div class="dataset-import-grid"><label>CSV / JSON 文件<input data-testid="scenario-dataset-file" type="file" accept=".csv,.json" :disabled="datasetBusy || dirty" @change="pickFile" /></label><label>数据集名称<input v-model="datasetName" :disabled="datasetBusy" placeholder="例如：搜索关键词" /></label></div><p>UTF-8 编码，最多 700 KB。CSV 首行为列名；JSON 使用对象数组。</p><button data-testid="scenario-dataset-import" type="button" class="secondary-command" :disabled="!file || !datasetName.trim() || datasetBusy || dirty" @click="importDataset">{{ datasetBusy ? '正在导入…' : '导入并绑定' }}</button></details>
      <p v-if="datasetError" role="alert" class="load-warning">{{ datasetError }}</p>
    </section>
    <header><h3>业务断言与完整步骤</h3><p>请求参数、业务断言、变量提取、数据绑定和清理步骤在这里统一编辑。应用后再进入确认步骤，保存时由平台校验完整执行契约。</p></header>
    <details><summary>配置字段说明</summary><dl>
      <dt>请求与业务结果</dt><dd><code>request</code> 配置服务、路径、参数和请求体；<code>assertions</code> 校验业务码或结果字段，<code>extractions</code> 保存后续步骤所需变量。</dd>
      <dt>数据字段绑定</dt><dd>在 <code>dataset_contract.variables</code> 声明列名，请求参数可使用 <code>{ "$data": "keyword" }</code> 取该列；提取变量引用使用 <code>{ "$extract": "model_id" }</code>。</dd>
      <dt>执行时机与清理</dt><dd><code>scope</code> 区分每轮 iteration、每用户 vu_once 与节点 agent_setup。当前写入仅支持每轮创建一个临时资源，绑定一个 cleanup_once；清理在每轮结束时执行，包括业务断言失败。资源 ID 必须从本轮创建响应提取，不能使用默认值。每用户和节点初始化仅支持只读，全局 setup_once 不支持。</dd>
      <dt>异步与中断边界</dt><dd>尚不支持自动轮询、更新已有资源或多资源补偿。创建超时不重发，没有本轮 ID 不执行删除；断电、失联或强制停止后需要人工核对清理。清理失败计入业务链路失败。</dd>
    </dl></details>
    <label>完整场景 JSON<textarea v-model="draft" data-testid="scenario-definition-json" rows="18" spellcheck="false" @input="edit" /></label>
    <p v-if="error" role="alert" class="load-warning">{{ error }}</p>
    <div class="advanced-actions"><span>{{ dirty ? '配置有未应用的修改，请先应用后继续。' : '当前完整配置已保留。' }}</span><button data-testid="scenario-definition-apply" class="secondary-command" type="button" @click="apply">应用配置</button></div>
  </section>
</template>

<style scoped>
.scenario-advanced { min-width: 0; display: grid; gap: 12px; }
.scenario-advanced h3, .scenario-advanced p { margin: 0; }
.scenario-advanced header { display: grid; gap: 6px; }
.scenario-advanced header p, .scenario-advanced dd, .advanced-actions span { color: #58657c; line-height: 1.7; font-size: 13px; }
.scenario-advanced label { display: grid; gap: 7px; min-width: 0; }
.scenario-advanced textarea { box-sizing: border-box; max-width: 100%; min-width: 0; width: 100%; resize: vertical; font: 12px/1.65 ui-monospace, SFMono-Regular, monospace; white-space: pre-wrap; overflow-wrap: anywhere; }
.scenario-advanced dt { font-weight: 650; margin-top: 8px; }
.scenario-advanced dd { margin: 4px 0 12px; }
.scenario-advanced code { overflow-wrap: anywhere; }
.advanced-actions { display: flex; align-items: center; justify-content: space-between; gap: 12px; flex-wrap: wrap; }
.advanced-actions button { flex-shrink: 0; }
.dataset-config { display: grid; gap: 10px; padding: 16px; border: 1px solid #dce4ef; border-radius: 12px; min-width: 0; }
.dataset-config > button { justify-self: start; }
.dataset-import-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 12px; margin: 12px 0; }
.dataset-config input, .dataset-config select { min-width: 0; width: 100%; box-sizing: border-box; }
@media (max-width: 640px) { .dataset-import-grid { grid-template-columns: 1fr; } }
</style>
