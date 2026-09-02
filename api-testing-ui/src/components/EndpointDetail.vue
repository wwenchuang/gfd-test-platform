<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { ExternalLink } from 'lucide-vue-next'

import type { ApiEndpoint } from '../api/contracts'

type DetailTab = 'definition' | 'request' | 'response' | 'cases' | 'history'
type OperationRecord = Record<string, unknown>

const props = defineProps<{ endpoint: ApiEndpoint | null }>()
const emit = defineEmits<{ 'open-history': [endpointId: string, endpointStableKey?: string] }>()
const tab = ref<DetailTab>('definition')
const operation = computed<OperationRecord>(() => props.endpoint?.operation || {})
const isDeveloping = computed(() => String(operation.value['x-apifox-status'] || '').toLowerCase() === 'developing')
const parameters = computed<OperationRecord[]>(() => Array.isArray(operation.value.parameters)
  ? operation.value.parameters.filter(item => item && typeof item === 'object') as OperationRecord[]
  : [])
const requestBody = computed<OperationRecord | null>(() => {
  const value = operation.value.requestBody
  return value && typeof value === 'object' ? value as OperationRecord : null
})
const responses = computed(() => {
  const value = operation.value.responses
  if (!value || typeof value !== 'object' || Array.isArray(value)) return []
  return Object.entries(value as Record<string, unknown>).map(([status, raw]) => {
    const response = raw && typeof raw === 'object' ? raw as OperationRecord : {}
    const content = response.content && typeof response.content === 'object' && !Array.isArray(response.content)
      ? Object.keys(response.content as Record<string, unknown>)
      : []
    return { status, description: String(response.description || '未提供说明'), content }
  })
})
const rawRequest = computed(() => ({ parameters: parameters.value, requestBody: requestBody.value }))
const parameterLocationLabels: Record<string, string> = {
  query: 'Query 参数', path: 'Path 参数', header: '请求头', cookie: 'Cookie',
}

watch(() => props.endpoint?.id, () => { tab.value = 'definition' })

function parameterLocation(value: unknown): string {
  const location = String(value || '')
  return parameterLocationLabels[location] || location || '参数'
}

function schemaType(parameter: OperationRecord): string {
  const schema = parameter.schema && typeof parameter.schema === 'object'
    ? parameter.schema as OperationRecord
    : {}
  return String(schema.type || schema.format || '未声明类型')
}

function contentTypes(body: OperationRecord | null): string[] {
  if (!body?.content || typeof body.content !== 'object' || Array.isArray(body.content)) return []
  return Object.keys(body.content as Record<string, unknown>)
}

function openHistory(): void {
  if (props.endpoint) emit('open-history', props.endpoint.id, props.endpoint.stable_key)
}
</script>

<template>
  <section class="endpoint-detail">
    <div v-if="!endpoint" class="state-message detail-empty">从左侧选择一个接口开始。</div>
    <template v-else>
      <header class="endpoint-title"><span :class="['method-badge', `method-${endpoint.method.toLowerCase()}`]">{{ endpoint.method }}</span><div><h2>{{ endpoint.summary || endpoint.path }}</h2><code>{{ endpoint.path }}</code></div><span v-if="isDeveloping" class="endpoint-status-badge">开发中</span></header>
      <p v-if="isDeveloping" data-testid="endpoint-development-warning" class="endpoint-development-warning" role="status"><strong>接口来源标记为开发中</strong>当前执行环境可能尚未上线。可以先设计用例，必须真实调试通过后才能采纳为基线。</p>
      <nav class="detail-tabs" aria-label="接口详情">
        <button v-for="item in [['definition','接口定义'],['request','请求参数'],['response','响应结构'],['cases','测试用例'],['history','执行记录']]" :key="item[0]" type="button" :class="{ active: tab === item[0] }" @click="tab = item[0] as DetailTab">{{ item[1] }}</button>
      </nav>
      <div class="detail-body">
        <dl v-if="tab === 'definition'" class="definition-list"><div><dt>操作标识</dt><dd>{{ endpoint.operation_id || '未提供' }}</dd></div><div><dt>标签</dt><dd>{{ endpoint.tags.join(' / ') || '未分组' }}</dd></div></dl>
        <section v-else-if="tab === 'request'" class="contract-summary">
          <article v-for="parameter in parameters" :key="`${parameter.in}-${parameter.name}`" class="contract-row">
            <span>{{ parameterLocation(parameter.in) }}</span><strong>{{ parameter.name }}</strong><code>{{ schemaType(parameter) }}</code><b :class="{ required: parameter.required }">{{ parameter.required ? '必填' : '可选' }}</b><small>{{ parameter.description || '未提供说明' }}</small>
          </article>
          <article v-if="requestBody" class="contract-row"><span>请求体</span><strong>{{ contentTypes(requestBody).join('、') || '已定义' }}</strong><b :class="{ required: requestBody.required }">{{ requestBody.required ? '必填' : '可选' }}</b><small>{{ requestBody.description || '按接口契约填写' }}</small></article>
          <p v-if="!parameters.length && !requestBody" class="compact-empty">该接口没有声明请求参数。</p>
          <details class="raw-contract"><summary>查看原始 JSON</summary><pre>{{ JSON.stringify(rawRequest, null, 2) }}</pre></details>
        </section>
        <section v-else-if="tab === 'response'" class="contract-summary">
          <article v-for="response in responses" :key="response.status" class="contract-row response-row"><span>HTTP</span><strong>{{ response.status }}</strong><code>{{ response.content.join('、') || '未声明媒体类型' }}</code><small>{{ response.description }}</small></article>
          <p v-if="!responses.length" class="compact-empty">接口契约没有声明响应结构。</p>
          <details class="raw-contract"><summary>查看原始 JSON</summary><pre>{{ JSON.stringify(operation.responses || {}, null, 2) }}</pre></details>
        </section>
        <p v-else-if="tab === 'cases'" class="state-message">下方编辑器展示当前草稿和已保存版本。</p>
        <section v-else class="history-entry"><p>前往执行记录页查看该接口跨版本参与的调试、回归和定时任务结果。</p><button data-testid="endpoint-open-history" type="button" class="secondary-command" @click="openHistory"><ExternalLink :size="14" />筛选该接口的执行记录</button></section>
      </div>
    </template>
  </section>
</template>
