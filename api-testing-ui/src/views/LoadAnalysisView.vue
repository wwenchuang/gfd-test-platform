<script setup lang="ts">
import { onMounted, ref, watch } from 'vue'
import LoadRunComparison from '../components/LoadRunComparison.vue'
import LoadPerformanceBaselines from '../components/LoadPerformanceBaselines.vue'
import { useContextStore } from '../stores/context'
import { useLoadTestingStore } from '../stores/loadTesting'
const context=useContextStore(), store=useLoadTestingStore()
const projectId=ref(''), busy=ref(false), error=ref('')
async function refresh() { if(!projectId.value)return;busy.value=true;error.value='';try{await store.loadRuns(projectId.value)}catch(e){error.value=e instanceof Error?e.message:'读取失败'}finally{busy.value=false} }
onMounted(async()=>{await Promise.all([context.loadSavedContext(),context.loadOptions()]);projectId.value=context.projectId||context.projects[0]?.id||''})
watch(projectId,refresh)
</script>
<template><main class="load-analysis"><header><div><small>性能测试</small><h1>性能分析</h1><p>单轮报告解释一次执行；这里核对多轮条件、结果差异及所选执行的接口覆盖。</p></div><button type="button" class="secondary-command" :disabled="busy" @click="refresh">{{ busy?'正在刷新…':'刷新记录' }}</button></header><label class="analysis-project">所属应用<select v-model="projectId"><option v-for="project in context.projects" :key="project.id" :value="project.id">{{ project.name }}</option></select></label><p v-if="error" role="alert">{{ error }}</p><p v-if="busy" role="status">正在加载当前应用的最近执行记录…</p><LoadRunComparison v-else :runs="store.runs" :project-id="projectId" /><LoadPerformanceBaselines :runs="store.runs" :project-id="projectId" /><p class="analysis-note">当前候选来自最近执行记录（最多 100 条）；覆盖只描述本次选中的执行，不代表全部历史或全业务均已完成性能测试。</p></main></template>
<style scoped>.load-analysis{padding:22px;display:grid;gap:18px}.load-analysis>header{display:flex;gap:12px;align-items:flex-start;justify-content:space-between}.load-analysis h1{font-size:26px;margin:6px 0 10px}.load-analysis small{color:#008c87;font-weight:600}.load-analysis p{font-size:14px;color:#617086;line-height:1.65}.analysis-project{display:grid;gap:7px;font-size:13px;max-width:350px}.analysis-project select{min-height:38px;padding:8px;border:1px solid #d8e2ec;border-radius:6px;background:white}.load-analysis [role=alert]{color:#b91c1c}.analysis-note{border-top:1px solid #d8e2ec;padding-top:12px}</style>
