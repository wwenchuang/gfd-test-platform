<script setup lang="ts">
import { computed, ref } from 'vue'
import { Archive, CheckCircle2, Database, RotateCcw } from 'lucide-vue-next'

import type { EnvironmentAsset, ProjectOption } from '../api/contracts'

interface ProjectEnvironmentStats {
  environmentCount: number
  activeCount: number
  archivedCount: number
  updatedAt: string | null
}

const props = defineProps<{
  projects: ProjectOption[]
  environments: EnvironmentAsset[]
  selectedProjectId: string
  selectedEnvironmentId: string
  status: 'active' | 'archived'
  projectStats?: Record<string, ProjectEnvironmentStats>
}>()

const emit = defineEmits<{
  'select-project': [projectId: string]
  'select-environment': [environmentId: string]
  'update:status': [status: 'active' | 'archived']
  archive: [environmentId: string]
  restore: [environmentId: string]
}>()

const searchQuery = ref('')

const filteredEnvironments = computed(() => {
  const keyword = searchQuery.value.trim().toLowerCase()
  if (!keyword) return props.environments
  return props.environments.filter(item => {
    return [item.name, item.description, String(item.revision)]
      .some(value => String(value || '').toLowerCase().includes(keyword))
  })
})

const environmentCountLabel = computed(() => {
  if (!searchQuery.value.trim()) return `${props.environments.length} 个`
  return `${filteredEnvironments.value.length} / ${props.environments.length} 个`
})

function projectEnvironmentLabel(projectId: string): string {
  const stats = props.projectStats?.[projectId]
  if (!stats || stats.environmentCount <= 0) return '暂无环境'
  const archived = stats.archivedCount > 0 ? ` · ${stats.archivedCount} 已归档` : ''
  const updated = stats.updatedAt ? ` · 更新 ${stats.updatedAt.slice(0, 10)}` : ''
  return `${stats.environmentCount} 个环境 · ${stats.activeCount} 活动${archived}${updated}`
}
</script>

<template>
  <div class="environment-asset-navigation">
    <aside class="environment-projects" aria-label="API 项目">
      <header><span>项目</span><small>{{ projects.length }}</small></header>
      <button
        v-for="project in projects"
        :key="project.id"
        type="button"
        class="environment-project-item"
        :class="{ active: selectedProjectId === project.id }"
        :data-project-id="project.id"
        @click="emit('select-project', project.id)"
      >
        <Database :size="16" />
        <span class="environment-project-copy">
          <strong>{{ project.name }}</strong>
          <small>{{ projectEnvironmentLabel(project.id) }}</small>
        </span>
      </button>
      <p v-if="!projects.length" class="environment-nav-empty">暂无项目</p>
    </aside>

    <section class="environment-assets" aria-label="环境资产">
      <header class="environment-assets-header">
        <div><strong>环境资产</strong><small>{{ environmentCountLabel }}</small></div>
        <div class="environment-status-tabs">
          <button
            type="button"
            :class="{ active: status === 'active' }"
            data-status="active"
            @click="emit('update:status', 'active')"
          >活动</button>
          <button
            type="button"
            :class="{ active: status === 'archived' }"
            data-status="archived"
            @click="emit('update:status', 'archived')"
          >已归档</button>
        </div>
      </header>

      <div class="environment-asset-search">
        <input
          v-model="searchQuery"
          data-environment-search
          type="search"
          placeholder="搜索环境名称或说明"
        />
      </div>

      <div class="environment-asset-items">
        <article
          v-for="environment in filteredEnvironments"
          :key="environment.id"
          class="environment-asset-item"
          :class="{ active: selectedEnvironmentId === environment.id }"
          :data-environment-id="environment.id"
          tabindex="0"
          @click="emit('select-environment', environment.id)"
          @keydown.enter="emit('select-environment', environment.id)"
        >
          <div class="environment-asset-title">
            <span class="environment-state-icon"><CheckCircle2 :size="15" /></span>
            <div><strong>{{ environment.name }}</strong><small>{{ environment.description || '暂无说明' }}</small></div>
            <span class="environment-version">v{{ environment.revision }}</span>
          </div>
          <div class="environment-asset-metrics">
            <span>{{ environment.service_count }} 个服务</span>
            <span>{{ environment.public_variable_count }} 个变量</span>
            <span>{{ environment.secret_count }} 个凭证</span>
          </div>
          <button
            v-if="environment.status === 'active'"
            type="button"
            class="environment-row-action"
            data-action="archive"
            title="归档环境"
            @click.stop="emit('archive', environment.id)"
          ><Archive :size="14" />归档</button>
          <button
            v-else
            type="button"
            class="environment-row-action"
            data-action="restore"
            title="恢复环境"
            @click.stop="emit('restore', environment.id)"
          ><RotateCcw :size="14" />恢复</button>
        </article>
        <div v-if="!filteredEnvironments.length" class="environment-list-empty">
          <Database :size="24" />
          <strong>{{ searchQuery ? '没有匹配环境' : (status === 'active' ? '暂无已保存环境' : '暂无已归档环境') }}</strong>
          <span>{{ searchQuery ? '换一个关键词，或切换项目查看。' : (status === 'active' ? '从 Apifox 手动同步或导入后会显示在这里。' : '归档的环境可在这里恢复。') }}</span>
        </div>
      </div>
    </section>
  </div>
</template>
