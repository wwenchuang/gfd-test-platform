import { createRouter, createWebHashHistory } from 'vue-router'

import AssetsView from './views/AssetsView.vue'
import BaselinesView from './views/BaselinesView.vue'
import CasesView from './views/CasesView.vue'
import ReportsView from './views/ReportsView.vue'
import RunsView from './views/RunsView.vue'
import ScheduledJobsView from './views/ScheduledJobsView.vue'
import SettingsView from './views/SettingsView.vue'
import TasksView from './views/TasksView.vue'
import WorkbenchView from './views/WorkbenchView.vue'

export const router = createRouter({
  history: createWebHashHistory(import.meta.env.BASE_URL),
  routes: [
    { path: '/', name: 'workbench', component: WorkbenchView },
    { path: '/tasks', name: 'tasks', component: TasksView },
    { path: '/cases', name: 'cases', component: CasesView },
    { path: '/assets', name: 'assets', component: AssetsView },
    { path: '/baselines', name: 'baselines', component: BaselinesView },
    { path: '/scheduled-jobs', name: 'scheduled-jobs', component: ScheduledJobsView },
    { path: '/runs', name: 'runs', component: RunsView },
    { path: '/reports', name: 'reports', component: ReportsView },
    { path: '/load-scenarios', name: 'load-scenarios', component: () => import('./views/LoadScenariosView.vue'), meta: { title: '性能场景' } },
    { path: '/load-runs', name: 'load-runs', component: () => import('./views/LoadRunsView.vue'), meta: { title: '压测执行' } },
    { path: '/load-reports', name: 'load-reports', component: () => import('./views/LoadPlaceholderView.vue'), meta: { title: '性能报告' } },
    { path: '/load-agents', name: 'load-agents', component: () => import('./views/LoadAgentsView.vue') },
    { path: '/settings', name: 'settings', component: SettingsView },
  ],
})
