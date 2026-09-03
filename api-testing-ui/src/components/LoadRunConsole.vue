<script setup lang="ts">
import { computed, ref } from 'vue'
import type { LoadRun, LoadRunEvent } from '../api/contracts'

const props = defineProps<{ run: LoadRun; events: LoadRunEvent[]; connectionState: string; busy?: boolean }>()
const emit = defineEmits<{ stop: [] }>()
const confirming = ref(false)
const terminal = computed(() => ['finished', 'failed', 'cancelled'].includes(props.run.state))
const stateLabel = computed(() => ({ draft: '等待检查', preflighting: '预检中', queued: '等待启动', starting: '节点准备中', running: '运行中', stopping: '停止中', finished: '已完成', failed: '失败', cancelled: '已取消' } as Record<string, string>)[props.run.state] || props.run.state)
const plannedAgents = computed(() => Array.isArray(props.run.configuration.agents) ? props.run.configuration.agents.length : 0)
const scenarioName = computed(() => {
  const value = props.run.configuration.scenario
  return value && typeof value === 'object' && 'name' in value ? String(value.name || props.run.id) : props.run.id
})
function message(event: LoadRunEvent): string { return String(event.payload.message || event.payload.state || event.type) }
function confirmStop(): void { confirming.value = false; emit('stop') }
</script>

<template>
  <section class="load-console" aria-label="压测实时控制台">
    <header><div><h2>实时执行</h2><p>{{ stateLabel }} · 计划 {{ plannedAgents }} 台节点 · {{ connectionState === 'polling' ? '实时连接中断，已切换轮询' : connectionState === 'open' ? '实时连接正常' : '正在同步状态' }}</p></div><button v-if="!terminal" data-testid="load-stop" class="danger-command" type="button" :disabled="busy || confirming" @click="confirming = true">停止压测</button></header>
    <div v-if="confirming" class="load-confirm"><p>确认停止“{{ scenarioName }}”？已完成的数据会保留，未完成部分不会自动迁移。</p><button type="button" class="secondary-command" @click="confirming = false">继续运行</button><button data-testid="load-stop-confirm" type="button" class="danger-command" @click="confirmStop">确认停止</button></div>
    <ol v-if="events.length" class="load-event-list"><li v-for="event in events" :key="event.id"><time>#{{ event.id }}</time><strong>{{ event.type }}</strong><span>{{ message(event) }}</span></li></ol>
    <p v-else class="compact-empty">暂无执行事件，启动后会自动显示节点和指标进度。</p>
  </section>
</template>
