<script setup lang="ts">
import { computed, onMounted } from 'vue'
import { Database, Save } from 'lucide-vue-next'

import { useContextStore } from '../stores/context'

const context = useContextStore()
const workspaceSummary = computed(() => context.projectId ? `项目 ${context.projectId}` : '未选择项目')

onMounted(() => context.loadSavedContext())
</script>

<template>
  <section class="workspace">
    <header class="page-toolbar">
      <div>
        <p class="eyebrow">API 测试</p>
        <h1>工作台</h1>
      </div>
      <button class="icon-command" type="button" title="保存工作区" :disabled="context.loading" @click="context.saveContext()">
        <Save :size="18" />
      </button>
    </header>

    <div class="context-strip" aria-live="polite">
      <Database :size="17" />
      <span>{{ context.loading ? '正在恢复已保存的工作区' : workspaceSummary }}</span>
      <span class="context-value">源版本 {{ context.sourceRevisionId || '未选择' }}</span>
      <span class="context-value">环境版本 {{ context.environmentRevisionId || '未选择' }}</span>
    </div>

    <p v-if="context.error" class="inline-error">{{ context.error }}</p>

    <div class="workbench-grid">
      <section class="panel endpoint-panel">
        <div class="panel-header"><h2>接口范围</h2><span>已保存来源</span></div>
        <div class="empty-state">选择项目后，在接口资产中定位要调试的接口。</div>
      </section>
      <section class="panel command-panel">
        <div class="panel-header"><h2>测试命令</h2><span>手动触发</span></div>
        <div class="empty-state">从接口资产选择接口后，可生成或编辑测试用例。</div>
      </section>
      <section class="panel console-panel">
        <div class="panel-header"><h2>执行日志</h2><span>等待任务</span></div>
        <pre class="console-output">尚无执行记录</pre>
      </section>
    </div>
  </section>
</template>
