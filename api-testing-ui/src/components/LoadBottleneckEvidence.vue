<script setup lang="ts">
import { computed } from 'vue'

type CoverageRow = {domain:string; label:string; status:string; evidence_ids:string[]; limitations:string[]; next_verification:string}
const props = defineProps<{rows: CoverageRow[]}>()
const visibleRows = computed(() => props.rows.slice(0, 9))
const counts = computed(() => {
  const rows = visibleRows.value
  return {available:rows.filter(r=>r.status==='available').length, partial:rows.filter(r=>r.status==='partial').length, missing:rows.filter(r=>r.status==='missing').length}
})
function statusLabel(status:string):string {
  return ({available:'证据可用',partial:'部分证据',missing:'待采集',not_applicable:'明确不适用'} as Record<string,string>)[status] || '待核对'
}
</script>

<template>
  <section class="bottleneck-coverage" aria-label="瓶颈证据覆盖">
    <h3>瓶颈证据覆盖</h3>
    <p class="coverage-note">覆盖状态说明本轮能观察到什么，不代表故障或根因已经确认。</p>
    <p v-if="!visibleRows.length" class="coverage-note">暂无证据覆盖清单，请在新报告中核对采集结果。</p>
    <details v-else>
      <summary>可用 {{ counts.available }} · 部分 {{ counts.partial }} · 待采集 {{ counts.missing }} <span>查看范围与下一步验证</span></summary>
      <div class="coverage-items">
        <article v-for="row in visibleRows" :key="row.domain">
          <header><strong>{{ row.label }}</strong><span>{{ statusLabel(row.status) }}</span></header>
          <ul><li v-for="(limitation,index) in row.limitations" :key="index">{{ limitation }}</li></ul>
          <p><b>下一步验证：</b>{{ row.next_verification }}</p>
          <small v-if="row.evidence_ids.length">证据引用：{{ row.evidence_ids.join('、') }}</small>
        </article>
      </div>
    </details>
  </section>
</template>

<style scoped>
.bottleneck-coverage{min-width:0;padding:14px 16px;border:1px solid #d8e2e9;border-radius:10px;background:#f8fafc;color:#334155;font-size:13px;line-height:1.6}.bottleneck-coverage h3{margin:0 0 6px;font-size:15px}.coverage-note{margin:4px 0 8px;color:#596a7b}.bottleneck-coverage summary{cursor:pointer;font-weight:600;overflow-wrap:anywhere}.bottleneck-coverage summary span{font-weight:400;margin-left:10px}.coverage-items{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,300px),1fr));gap:10px;margin-top:12px}.coverage-items article{min-width:0;padding:12px;border:1px solid #dbe3e9;border-radius:8px;background:white;overflow-wrap:anywhere}.coverage-items header{display:flex;align-items:center;justify-content:space-between;gap:12px}.coverage-items header span{font-size:12px;border-radius:4px;background:#edf2f6;padding:2px 6px;flex-shrink:0}.coverage-items ul{margin:8px 0;padding-left:18px}.coverage-items p{margin:6px 0}.coverage-items small{color:#657587}
</style>
