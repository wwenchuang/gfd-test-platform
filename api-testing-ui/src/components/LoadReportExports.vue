<script setup lang="ts">
import { ref } from 'vue'
import { apiClient } from '../api/client'
const props = defineProps<{ runId: string; ready: boolean }>()
const busy = ref(''); const error = ref('')
async function download(format: string) {
 if (!props.ready || busy.value) return
 busy.value = format; error.value = ''
 try {
   const result = await apiClient.get<{filename: string; mime_type: string; content_base64: string}>(`/api/api-testing/v1/load-runs/${encodeURIComponent(props.runId)}/report-export?format=${format}`)
   const file = result.data; const bytes = Uint8Array.from(atob(file.content_base64), char => char.charCodeAt(0))
   const url = URL.createObjectURL(new Blob([bytes], { type: file.mime_type })); const a = document.createElement('a'); a.href = url; a.download = file.filename; document.body.append(a); a.click(); a.remove(); window.setTimeout(() => URL.revokeObjectURL(url), 1000)
 } catch(e) { error.value = e instanceof Error ? e.message : '导出失败，请重试' } finally { busy.value = '' }
}
</script>
<template><section class="report-exports" aria-label="正式报告导出"><div><button type="button" class="secondary-command" :disabled="!ready || Boolean(busy)" @click="download('docx')">{{ busy === 'docx' ? '正在生成 Word…' : '下载 Word 汇报稿' }}</button><button type="button" class="secondary-command" :disabled="!ready || Boolean(busy)" @click="download('xlsx')">{{ busy === 'xlsx' ? '正在生成 Excel…' : '下载 Excel 明细' }}</button></div><small v-if="!ready">执行及监控采集结束后可导出最终报告。</small><p v-if="error" role="alert">{{ error }}</p></section></template>
<style scoped>.report-exports{display:grid;gap:8px;margin:12px 0}.report-exports>div{display:flex;gap:8px;flex-wrap:wrap}.report-exports small{font-size:13px;color:#64748b}.report-exports [role=alert]{color:#b91c1c}</style>
