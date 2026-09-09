import { createRouter, createWebHashHistory } from 'vue-router'

export const router = createRouter({
  history: createWebHashHistory(import.meta.env.BASE_URL),
  routes: [
    { path: '/', name: 'workbench', component: () => import('./views/WorkbenchView.vue') },
    { path: '/tasks', name: 'tasks', component: () => import('./views/TasksView.vue') },
    { path: '/cases', name: 'cases', component: () => import('./views/CasesView.vue') },
    { path: '/assets', name: 'assets', component: () => import('./views/AssetsView.vue') },
    { path: '/baselines', name: 'baselines', component: () => import('./views/BaselinesView.vue') },
    { path: '/scheduled-jobs', name: 'scheduled-jobs', component: () => import('./views/ScheduledJobsView.vue') },
    { path: '/runs', name: 'runs', component: () => import('./views/RunsView.vue') },
    { path: '/reports', name: 'reports', component: () => import('./views/ReportsView.vue') },
    { path: '/load-scenarios', name: 'load-scenarios', component: () => import('./views/LoadScenariosView.vue'), meta: { title: '性能场景' } },
    { path: '/load-runs', name: 'load-runs', component: () => import('./views/LoadRunsView.vue'), meta: { title: '压测执行' } },
    { path: '/load-schedules', name: 'load-schedules', component: () => import('./views/LoadSchedulesView.vue'), meta: { title: '性能定时计划' } },
    { path: '/load-analysis', name: 'load-analysis', component: () => import('./views/LoadAnalysisView.vue'), meta: { title: '性能分析' } },
    { path: '/load-reports', name: 'load-reports', component: () => import('./views/LoadReportsView.vue'), meta: { title: '性能报告' } },
    { path: '/load-agents', name: 'load-agents', component: () => import('./views/LoadAgentsView.vue') },
    { path: '/settings', name: 'settings', component: () => import('./views/SettingsView.vue') },
  ],
})
