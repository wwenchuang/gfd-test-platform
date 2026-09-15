<script setup lang="ts">
defineProps<{ samples: Array<Record<string, unknown>>; steps: Array<Record<string, unknown>> }>()
const kinds: Record<string, string> = { http_error: '请求异常', business_assertion: '断言失败', workflow_failure: '链路失败' }
function time(value: unknown): string {
  const date = new Date(String(value || ''))
  return Number.isNaN(date.getTime()) ? '未记录' : date.toLocaleString('zh-CN', { hour12: false })
}
</script>

<template>
  <section v-if="samples.length" class="load-failure-samples">
    <h2>失败样本与发生时间</h2>
    <p class="report-explainer">这是保留的有限样本，不等于全部失败次数。状态未记录时不能区分网络异常与服务拒绝；同一请求可能同时产生请求、断言和链路失败。</p>
    <div class="report-table-scroll"><table>
      <thead><tr><th>发生时间</th><th>步骤</th><th>失败类型</th><th>响应状态</th><th>采集器错误码</th><th>断言说明</th></tr></thead>
      <tbody><tr v-for="(sample, index) in samples" :key="index">
        <td>{{ time(sample.observed_at) }}</td>
        <td>{{ steps.find(step => step.id === sample.step_id)?.name || (sample.step_id === 'all' ? '完整链路' : sample.step_id) }}</td>
        <td>{{ kinds[String(sample.kind)] || '其他异常' }}</td>
        <td>{{ sample.status_code === 0 ? '未收到 HTTP 响应' : sample.status_code == null ? '未记录' : `HTTP ${sample.status_code}` }}</td>
        <td>{{ sample.error_code ?? '未记录' }}</td>
        <td>{{ sample.summary || '未记录' }}</td>
      </tr></tbody>
    </table></div>
  </section>
</template>
