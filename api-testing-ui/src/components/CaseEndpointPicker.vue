<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { FilePlus2, PencilLine, Search, Sparkles, X } from 'lucide-vue-next'

import type { ApiEndpoint } from '../api/contracts'
import { groupEndpointDomains, groupEndpoints } from '../utils/endpointGroups'

const props = withDefaults(defineProps<{
  endpoints: ApiEndpoint[]
  caseCountByEndpoint: Record<string, number>
  busy?: boolean
}>(), { busy: false })

const emit = defineEmits<{
  close: []
  'create-manual': [endpoint: ApiEndpoint]
  'generate-basic': [endpoint: ApiEndpoint]
  'generate-ai': [endpoint: ApiEndpoint]
}>()

const query = ref('')
const activeEndpointId = ref('')
const activeDomain = ref('')
const keyword = computed(() => query.value.trim().toLocaleLowerCase())
const domainOptions = computed(() => groupEndpointDomains(groupEndpoints(props.endpoints)))
const useDomainNavigation = computed(() => groupEndpoints(props.endpoints).length > 8)
const matchedEndpoints = computed(() => {
  let items = keyword.value
    ? props.endpoints.filter(endpoint => [
      endpoint.summary, endpoint.method, endpoint.path, ...endpoint.tags,
    ].join(' ').toLocaleLowerCase().includes(keyword.value))
    : props.endpoints
  if (!keyword.value && useDomainNavigation.value && activeDomain.value) {
    items = domainOptions.value.find(domain => domain.name === activeDomain.value)?.endpoints || []
  }
  return items.slice(0, 50)
})
const activeEndpoint = computed(() => props.endpoints.find(item => item.id === activeEndpointId.value) || null)
const activeEndpointDeveloping = computed(() => String(activeEndpoint.value?.operation?.['x-apifox-status'] || '').toLowerCase() === 'developing')

watch(domainOptions, domains => {
  if (!useDomainNavigation.value) {
    activeDomain.value = ''
    return
  }
  if (!domains.some(domain => domain.name === activeDomain.value)) activeDomain.value = domains[0]?.name || ''
}, { immediate: true })

function caseState(endpoint: ApiEndpoint): string {
  const count = props.caseCountByEndpoint[endpoint.id] || 0
  return count ? `已有 ${count} 条用例` : '暂无用例'
}

function selectDomain(domain: string): void {
  activeDomain.value = domain
  if (!domainOptions.value.find(item => item.name === domain)?.endpoints.some(item => item.id === activeEndpointId.value)) {
    activeEndpointId.value = ''
  }
}
</script>

<template>
  <section class="case-endpoint-picker" aria-label="选择接口创建用例">
    <header>
      <div><strong>选择接口创建用例</strong><span>{{ endpoints.length }} 个接口</span></div>
      <button class="mini-icon" type="button" title="关闭接口选择" @click="emit('close')"><X :size="17" /></button>
    </header>
    <label class="search-box case-endpoint-search"><Search :size="15" /><span class="sr-only">搜索接口</span><input v-model="query" data-testid="case-endpoint-search" placeholder="搜索接口名称、路径或分组" /></label>
    <div v-if="useDomainNavigation && !keyword" class="case-endpoint-domains" aria-label="接口业务范围">
      <button
        v-for="domain in domainOptions"
        :key="domain.name"
        :data-testid="`case-endpoint-domain-${domain.name}`"
        type="button"
        :class="{ active: activeDomain === domain.name }"
        @click="selectDomain(domain.name)"
      >{{ domain.name }} <span>{{ domain.endpoints.length }}</span></button>
    </div>
    <div class="case-endpoint-picker-body">
      <div class="case-endpoint-results">
        <button
          v-for="endpoint in matchedEndpoints"
          :key="endpoint.id"
          :data-testid="`case-endpoint-${endpoint.id}`"
          type="button"
          :class="{ active: endpoint.id === activeEndpointId }"
          @click="activeEndpointId = endpoint.id"
        >
          <span :class="['method-badge', `method-${endpoint.method.toLowerCase()}`]">{{ endpoint.method }}</span>
          <span><strong>{{ endpoint.summary || endpoint.path }}</strong><small>{{ endpoint.path }}</small></span>
          <em>{{ caseState(endpoint) }}</em>
        </button>
        <p v-if="!matchedEndpoints.length" class="section-empty">没有匹配的接口</p>
        <p v-else-if="!keyword && (useDomainNavigation ? (domainOptions.find(domain => domain.name === activeDomain)?.endpoints.length || 0) : endpoints.length) > matchedEndpoints.length" class="case-endpoint-limit">{{ useDomainNavigation ? '当前业务范围展示前 50 个接口' : '当前展示前 50 个接口' }}，请使用搜索定位其他接口。</p>
      </div>
      <div class="case-endpoint-actions">
        <template v-if="activeEndpoint">
          <div><span :class="['method-badge', `method-${activeEndpoint.method.toLowerCase()}`]">{{ activeEndpoint.method }}</span><strong>{{ activeEndpoint.summary || activeEndpoint.path }}</strong></div>
          <code>{{ activeEndpoint.path }}</code>
          <p>{{ (caseCountByEndpoint[activeEndpoint.id] || 0) ? `该接口已有 ${caseCountByEndpoint[activeEndpoint.id]} 条用例，可继续新增场景。` : '该接口暂无用例。' }}</p>
          <p v-if="activeEndpointDeveloping" data-testid="case-endpoint-development-warning" class="endpoint-development-warning" role="status"><strong>接口来源标记为开发中</strong>当前环境可能尚未上线。可先准备候选，调试通过后再纳入基线。</p>
          <button data-testid="case-endpoint-create-manual" class="secondary-command wide" type="button" :disabled="busy" @click="emit('create-manual', activeEndpoint)"><PencilLine :size="16" />新建手工用例</button>
          <button data-testid="case-endpoint-generate-basic" class="secondary-command wide" type="button" :disabled="busy" @click="emit('generate-basic', activeEndpoint)"><FilePlus2 :size="16" />生成基础正向候选</button>
          <button data-testid="case-endpoint-generate-ai" class="primary-command wide" type="button" :disabled="busy" @click="emit('generate-ai', activeEndpoint)"><Sparkles :size="16" />AI 生成测试用例</button>
        </template>
        <p v-else class="section-empty">输入接口名称、路径或分组进行搜索，然后选择一个接口。</p>
      </div>
    </div>
  </section>
</template>
