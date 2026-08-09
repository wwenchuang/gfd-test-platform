<script setup lang="ts">
import { computed, ref } from 'vue'

import type { ApiEndpoint } from '../api/contracts'

const props = defineProps<{ endpoint: ApiEndpoint | null }>()
const tab = ref<'definition' | 'request' | 'response' | 'cases' | 'history'>('definition')
const operation = computed(() => props.endpoint?.operation || {})
</script>

<template>
  <section class="endpoint-detail">
    <div v-if="!endpoint" class="state-message detail-empty">从左侧选择一个接口开始。</div>
    <template v-else>
      <header class="endpoint-title"><span :class="['method-badge', `method-${endpoint.method.toLowerCase()}`]">{{ endpoint.method }}</span><div><h2>{{ endpoint.summary || endpoint.path }}</h2><code>{{ endpoint.path }}</code></div></header>
      <nav class="detail-tabs" aria-label="接口详情">
        <button v-for="item in [['definition','接口定义'],['request','请求参数'],['response','响应结构'],['cases','测试用例'],['history','执行记录']]" :key="item[0]" type="button" :class="{ active: tab === item[0] }" @click="tab = item[0] as typeof tab">{{ item[1] }}</button>
      </nav>
      <div class="detail-body">
        <dl v-if="tab === 'definition'" class="definition-list"><div><dt>Operation ID</dt><dd>{{ endpoint.operation_id || '未提供' }}</dd></div><div><dt>标签</dt><dd>{{ endpoint.tags.join(' / ') || '未分组' }}</dd></div></dl>
        <pre v-else-if="tab === 'request'">{{ JSON.stringify({ parameters: operation.parameters || [], requestBody: operation.requestBody || null }, null, 2) }}</pre>
        <pre v-else-if="tab === 'response'">{{ JSON.stringify(operation.responses || {}, null, 2) }}</pre>
        <p v-else-if="tab === 'cases'" class="state-message">下方编辑器展示当前草稿和已保存版本。</p>
        <p v-else class="state-message">调试完成后，执行记录会保留在平台中。</p>
      </div>
    </template>
  </section>
</template>
