<script setup lang="ts">
import { computed, nextTick, ref } from 'vue'
import { useRoute } from 'vue-router'
import { Activity, Boxes, CalendarClock, ChartLine, ClipboardList, FileCode2, FlaskConical, Gauge, History, ListChecks, Menu, Server, Settings2, ShieldCheck, X } from 'lucide-vue-next'
import { apiTestingHasPermission } from './utils/authRedirect'

const navigationSections = computed(() => [
  {
    id: 'design',
    label: '设计准备',
    items: [
      { to: '/', label: '工作台', icon: FlaskConical },
      { to: '/assets', label: '接口资产', icon: FileCode2 },
      { to: '/cases', label: '用例管理', icon: ClipboardList, testId: 'nav-cases' },
    ],
  },
  ...(apiTestingHasPermission('api.loadtest.view') ? [{
    id: 'load-testing',
    label: '性能测试',
    items: [
      { to: '/load-scenarios', label: '性能场景', icon: Gauge },
      { to: '/load-runs', label: '压测执行', icon: Activity },
      { to: '/load-reports', label: '性能报告', icon: ChartLine },
      { to: '/load-agents', label: '压测节点', icon: Server },
    ],
  }] : []),
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
])

const route = useRoute()
const mobileNavigationOpen = ref(false)
const navigationToggle = ref<HTMLButtonElement | null>(null)
const navigationClose = ref<HTMLButtonElement | null>(null)
const currentPageLabel = computed(() => navigationSections
  .value
  .flatMap(section => section.items)
  .find(item => item.to === route.path)?.label || 'API 测试')

async function openNavigation(): Promise<void> {
  mobileNavigationOpen.value = true
  await nextTick()
  navigationClose.value?.focus()
}

async function closeNavigation(): Promise<void> {
  mobileNavigationOpen.value = false
  await nextTick()
  navigationToggle.value?.focus()
}
</script>

<template>
  <div class="app-shell">
    <header class="mobile-app-bar">
      <button
        ref="navigationToggle"
        data-testid="mobile-nav-toggle"
        class="mobile-nav-toggle"
        type="button"
        aria-label="打开导航"
        :aria-expanded="mobileNavigationOpen"
        aria-controls="api-testing-navigation"
        @click="openNavigation"
      ><Menu :size="20" /></button>
      <strong>{{ currentPageLabel }}</strong>
      <a href="/task-manager.html" title="返回任务平台"><FlaskConical :size="18" /></a>
    </header>
    <button v-if="mobileNavigationOpen" class="mobile-nav-backdrop" type="button" aria-label="关闭导航" @click="closeNavigation" />
    <aside id="api-testing-navigation" :class="['side-rail', { 'mobile-open': mobileNavigationOpen }]" aria-label="API 测试导航" @keydown.esc.stop="closeNavigation">
      <div class="rail-brand-row">
      <a class="brand" href="/task-manager.html" title="返回任务平台"><FlaskConical :size="19" /></a>
        <button ref="navigationClose" class="mobile-nav-close" type="button" aria-label="关闭导航" @click="closeNavigation"><X :size="19" /></button>
      </div>
      <nav class="rail-nav">
        <section
          v-for="section in navigationSections"
          :key="section.id"
          class="rail-section"
          :data-testid="`nav-section-${section.id}`"
        >
          <h2 class="rail-section-label">{{ section.label }}</h2>
          <RouterLink v-for="item in section.items" :key="item.to" :to="item.to" class="rail-link" :title="item.label" :data-testid="item.testId" @click="closeNavigation">
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
