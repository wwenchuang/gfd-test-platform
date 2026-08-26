<script setup lang="ts">
import { computed, ref } from 'vue'
import { ChevronDown, Database, Save } from 'lucide-vue-next'

import type { EnvironmentRevisionOption, ProjectOption, SourceRevisionOption } from '../api/contracts'
import { isProductionLikeEnvironment } from '../utils/executionConfirmation'

const props = withDefaults(defineProps<{
  projects?: ProjectOption[]
  sourceRevisions?: SourceRevisionOption[]
  environmentRevisions?: EnvironmentRevisionOption[]
  projectId: string | null
  sourceRevisionId: string | null
  environmentRevisionId: string | null
  loading?: boolean
  saved?: boolean
  saveLabel?: string
  savedLabel?: string
}>(), {
  projects: () => [], sourceRevisions: () => [], environmentRevisions: () => [], loading: false, saved: false,
  saveLabel: '保存测试范围', savedLabel: '范围已保存',
})

const emit = defineEmits<{
  'update:projectId': [value: string | null]
  'update:sourceRevisionId': [value: string | null]
  'update:environmentRevisionId': [value: string | null]
  save: []
}>()
const expanded = ref(false)

const sources = computed(() => {
  const list = props.sourceRevisions.filter(item => item.project_id === props.projectId)
  if (props.sourceRevisionId && !list.some(item => item.id === props.sourceRevisionId)) {
    return [
      ...list,
      {
        id: props.sourceRevisionId,
        source_id: props.sourceRevisionId,
        project_id: props.projectId || '',
        name: '当前任务接口版本',
        revision_number: 0,
        endpoint_count: 0,
      },
    ]
  }
  return list
})
const environments = computed(() => {
  const list = props.environmentRevisions.filter(item => item.project_id === props.projectId)
  if (props.environmentRevisionId && !list.some(item => item.id === props.environmentRevisionId)) {
    return [
      ...list,
      {
        id: props.environmentRevisionId,
        environment_id: props.environmentRevisionId,
        project_id: props.projectId || '',
        name: '当前执行环境',
        revision: 0,
      },
    ]
  }
  return list
})
const selectedProjectName = computed(() => props.projects.find(item => item.id === props.projectId)?.name || '未选择项目')
const selectedEnvironmentName = computed(() => environments.value.find(item => item.id === props.environmentRevisionId)?.name || '未选择环境')
const productionEnvironment = computed(() => isProductionLikeEnvironment(selectedEnvironmentName.value))

function sourceLabel(source: SourceRevisionOption): string {
  if (!source.revision_number && !source.endpoint_count) return `${source.name} · 已保存任务引用`
  return `${source.name} · v${source.revision_number} · ${source.endpoint_count} 个接口`
}

function environmentLabel(environment: EnvironmentRevisionOption): string {
  if (!environment.revision) return `${environment.name} · 已保存任务引用`
  return `${environment.name} · v${environment.revision}`
}

function nullable(value: string): string | null {
  return value || null
}
</script>

<template>
  <section :class="['context-bar', { 'context-expanded': expanded }]" aria-label="当前测试范围">
    <div class="context-heading">
      <Database :size="17" /><strong>测试范围</strong>
      <span v-if="productionEnvironment" data-testid="production-environment-warning" class="production-environment-warning">生产环境 · 执行前确认</span>
    </div>
    <p data-testid="context-summary" class="context-summary"><strong>{{ selectedProjectName }}</strong><span>{{ selectedEnvironmentName }}</span></p>
    <button
      data-testid="context-toggle"
      class="context-toggle"
      type="button"
      :aria-expanded="expanded"
      @click="expanded = !expanded"
    >调整范围<ChevronDown :size="15" :class="{ rotated: expanded }" /></button>
    <div class="context-selectors">
      <label>项目
        <select data-testid="context-project" :value="projectId || ''" :disabled="loading" @change="emit('update:projectId', nullable(($event.target as HTMLSelectElement).value))">
          <option value="">选择项目</option>
          <option v-for="project in projects" :key="project.id" :value="project.id">{{ project.name }}</option>
        </select>
      </label>
      <label>接口版本
        <select data-testid="context-source" :value="sourceRevisionId || ''" :disabled="loading || !projectId" @change="emit('update:sourceRevisionId', nullable(($event.target as HTMLSelectElement).value))">
          <option value="">{{ sources.length ? '选择已保存接口' : '暂无已保存接口' }}</option>
          <option v-for="source in sources" :key="source.id" :value="source.id">{{ sourceLabel(source) }}</option>
        </select>
      </label>
      <label>执行环境
        <select data-testid="context-environment" :value="environmentRevisionId || ''" :disabled="loading || !projectId" @change="emit('update:environmentRevisionId', nullable(($event.target as HTMLSelectElement).value))">
          <option value="">{{ environments.length ? '选择执行环境' : '暂无已保存环境' }}</option>
          <option v-for="environment in environments" :key="environment.id" :value="environment.id">{{ environmentLabel(environment) }}</option>
        </select>
      </label>
    </div>
    <a v-if="!projectId" class="context-next" href="#/assets">先创建项目</a>
    <a v-else-if="!sources.length" class="context-next" href="#/assets">先保存接口来源</a>
    <a v-else-if="!environments.length" class="context-next" href="#/settings">先配置执行环境</a>
    <span v-else-if="saved && projectId && sourceRevisionId && environmentRevisionId" class="saved-state" role="status">{{ savedLabel }}</span>
    <button class="context-save" type="button" :disabled="loading || !projectId || !sourceRevisionId || !environmentRevisionId" @click="emit('save')"><Save :size="16" />{{ saveLabel }}</button>
  </section>
</template>
