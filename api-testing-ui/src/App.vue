<script setup lang="ts">
import { Boxes, CalendarClock, ClipboardList, FileCode2, FlaskConical, History, ListChecks, Settings2, ShieldCheck } from 'lucide-vue-next'

const navigationSections = [
  {
    id: 'design',
    label: '设计准备',
    items: [
      { to: '/', label: '工作台', icon: FlaskConical },
      { to: '/assets', label: '接口资产', icon: FileCode2 },
      { to: '/cases', label: '用例管理', icon: ClipboardList, testId: 'nav-cases' },
    ],
  },
  {
    id: 'regression',
    label: '回归编排',
    items: [
      { to: '/tasks', label: '任务管理', icon: ListChecks, testId: 'nav-tasks' },
      { to: '/baselines', label: '基线用例', icon: ShieldCheck },
      { to: '/scheduled-jobs', label: '定时任务', icon: CalendarClock },
    ],
  },
  {
    id: 'results',
    label: '结果分析',
    items: [
      { to: '/runs', label: '执行记录', icon: History },
      { to: '/reports', label: '测试报告', icon: Boxes },
    ],
  },
  {
    id: 'settings',
    label: '项目配置',
    items: [
      { to: '/settings', label: '环境配置', icon: Settings2 },
    ],
  },
]
</script>

<template>
  <div class="app-shell">
    <aside class="side-rail" aria-label="API 测试导航">
      <a class="brand" href="/task-manager.html" title="返回任务平台"><FlaskConical :size="19" /></a>
      <nav class="rail-nav">
        <section
          v-for="section in navigationSections"
          :key="section.id"
          class="rail-section"
          :data-testid="`nav-section-${section.id}`"
        >
          <h2 class="rail-section-label">{{ section.label }}</h2>
          <RouterLink v-for="item in section.items" :key="item.to" :to="item.to" class="rail-link" :title="item.label" :data-testid="item.testId">
            <component :is="item.icon" :size="18" stroke-width="1.8" />
            <span>{{ item.label }}</span>
          </RouterLink>
        </section>
      </nav>
    </aside>
    <main class="main-content">
      <RouterView />
    </main>
  </div>
</template>
