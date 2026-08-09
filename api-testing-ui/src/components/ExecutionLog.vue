<script setup lang="ts">
import { computed, nextTick, ref, watch } from 'vue'
import { ChevronDown, ChevronRight, CirclePause, CirclePlay, Search } from 'lucide-vue-next'

import type { ExecutionConnectionState, ExecutionEventView } from '../api/contracts'

const props = withDefaults(defineProps<{ events: ExecutionEventView[]; connectionState?: ExecutionConnectionState }>(), {
  connectionState: 'idle',
})
const level = ref('all')
const caseId = ref('all')
const following = ref(true)
const output = ref<HTMLElement | null>(null)
const expanded = ref(new Set<number>())
const caseOptions = computed(() => [...new Set(props.events.map(item => item.caseId).filter(Boolean))])
const filtered = computed(() => props.events.filter(item => (
  (level.value === 'all' || item.level === level.value)
  && (caseId.value === 'all' || item.caseId === caseId.value)
)))
const connectionLabel = computed(() => ({
  idle: '未连接', connecting: '正在连接', open: '实时连接', reconnecting: '正在重连', complete: '执行已结束', failed: '连接失败',
}[props.connectionState]))

watch(() => props.events.length, async () => {
  if (!following.value) return
  await nextTick()
  if (output.value) output.value.scrollTop = output.value.scrollHeight
})

function toggleEvidence(id: number): void {
  const next = new Set(expanded.value)
  if (next.has(id)) next.delete(id)
  else next.add(id)
  expanded.value = next
}
</script>

<template>
  <section class="execution-log panel">
    <header class="panel-header"><div><h2>实时日志</h2><span :class="`connection-${connectionState}`">{{ connectionLabel }}</span></div><button data-testid="log-follow" class="mini-icon" type="button" :title="following ? '暂停自动滚动' : '继续跟随最新日志'" @click="following = !following"><CirclePause v-if="following" :size="15" /><CirclePlay v-else :size="15" /></button></header>
    <div class="log-tools"><label><Search :size="14" /><span class="sr-only">日志级别</span><select v-model="level" data-testid="log-level"><option value="all">全部级别</option><option value="info">信息</option><option value="success">通过</option><option value="warning">提醒</option><option value="error">失败</option></select></label><label><span class="sr-only">用例</span><select v-model="caseId"><option value="all">全部用例</option><option v-for="item in caseOptions" :key="item" :value="item">{{ item.slice(0, 8) }}</option></select></label><span v-if="!following" class="paused-label">滚动已暂停</span></div>
    <div ref="output" class="log-output" aria-live="polite">
      <div v-for="event in filtered" :key="event.id" data-testid="log-line" :class="['log-line', `log-${event.level}`]"><time>#{{ event.id }}</time><strong>{{ event.message }}</strong><code v-if="event.caseId">{{ event.caseId.slice(0, 8) }}</code><button v-if="Object.keys(event.payload).length" data-testid="log-evidence-toggle" class="log-evidence-toggle" type="button" :title="expanded.has(event.id) ? '收起事件证据' : '查看事件证据'" @click="toggleEvidence(event.id)"><ChevronDown v-if="expanded.has(event.id)" :size="13" /><ChevronRight v-else :size="13" /></button><pre v-if="expanded.has(event.id)" data-testid="log-evidence">{{ JSON.stringify(event.payload, null, 2) }}</pre></div>
      <p v-if="!filtered.length" class="state-message">暂无匹配日志</p>
    </div>
  </section>
</template>
