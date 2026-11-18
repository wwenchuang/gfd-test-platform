<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { apiClient } from '../api/client'
import '../../../js/failure-analysis.js'
import '../../../css/failure-analysis.css'

type RequestOptions = { method?: string; body?: unknown; signal?: AbortSignal }
type AnalysisView = { refresh: () => Promise<void>; dispose: () => void }
type AnalysisWidget = {
  mount: (host: HTMLElement, options: {
    source: 'api'; runId: string; identity: () => string
    request: (path: string, options?: RequestOptions) => Promise<unknown>
  }) => AnalysisView
}
const props = defineProps<{ runId: string }>()
const host = ref<HTMLElement | null>(null)
let view: AnalysisView | null = null
const identity = () => sessionStorage.getItem('sessionToken') || ''
function mountAnalysis(): void {
  view?.dispose()
  if (!host.value) return
  const widget = (window as Window & { FailureAnalysis?: AnalysisWidget }).FailureAnalysis
  view = widget?.mount(host.value, {
    source: 'api', runId: props.runId, identity,
    request: (path, options = {}) => options.method === 'POST'
      ? apiClient.post('/api' + path, options.body, { signal: options.signal })
      : apiClient.get('/api' + path, { signal: options.signal }),
  }) || null
}
onMounted(mountAnalysis)
watch(() => props.runId, mountAnalysis)
onBeforeUnmount(() => { view?.dispose(); view = null })
</script>

<template>
  <div ref="host" data-api-failure-analysis />
</template>
