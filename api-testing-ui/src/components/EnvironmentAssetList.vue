<script setup lang="ts">
import { Archive, CheckCircle2, Database, RotateCcw } from 'lucide-vue-next'

import type { EnvironmentAsset, ProjectOption } from '../api/contracts'

defineProps<{
  projects: ProjectOption[]
  environments: EnvironmentAsset[]
  selectedProjectId: string
  selectedEnvironmentId: string
  status: 'active' | 'archived'
}>()

const emit = defineEmits<{
  'select-project': [projectId: string]
  'select-environment': [environmentId: string]
  'update:status': [status: 'active' | 'archived']
  archive: [environmentId: string]
  restore: [environmentId: string]
}>()
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
        <span>{{ project.name }}</span>
      </button>
      <p v-if="!projects.length" class="environment-nav-empty">暂无项目</p>
    </aside>

    <section class="environment-assets" aria-label="环境资产">
      <header class="environment-assets-header">
        <div><strong>环境资产</strong><small>{{ environments.length }} 个</small></div>
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

      <div class="environment-asset-items">
        <article
          v-for="environment in environments"
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
        <div v-if="!environments.length" class="environment-list-empty">
          <Database :size="24" />
          <strong>{{ status === 'active' ? '暂无已保存环境' : '暂无已归档环境' }}</strong>
          <span>{{ status === 'active' ? '从 Apifox 手动同步或导入后会显示在这里。' : '归档的环境可在这里恢复。' }}</span>
        </div>
      </div>
    </section>
  </div>
</template>
