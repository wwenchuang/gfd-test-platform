<script setup lang="ts">
import { Boxes, FileCode2, FlaskConical, History, Settings2 } from 'lucide-vue-next'

const navigation = [
  { to: '/', label: '工作台', icon: FlaskConical },
  { to: '/assets', label: '接口资产', icon: FileCode2 },
  { to: '/runs', label: '执行记录', icon: History },
  { to: '/reports', label: '测试报告', icon: Boxes },
  { to: '/settings', label: '环境配置', icon: Settings2 },
]
</script>

<template>
  <div class="app-shell">
    <aside class="side-rail" aria-label="API 测试导航">
      <a class="brand" href="/task-manager.html" title="返回任务平台"><FlaskConical :size="19" /></a>
      <nav class="rail-nav">
        <RouterLink v-for="item in navigation" :key="item.to" :to="item.to" class="rail-link" :title="item.label">
          <component :is="item.icon" :size="18" stroke-width="1.8" />
          <span>{{ item.label }}</span>
        </RouterLink>
      </nav>
    </aside>
    <main class="main-content">
      <RouterView />
    </main>
  </div>
</template>
