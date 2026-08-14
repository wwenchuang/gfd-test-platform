import { createRouter, createWebHashHistory } from 'vue-router'

import AssetsView from './views/AssetsView.vue'
import BaselinesView from './views/BaselinesView.vue'
import ReportsView from './views/ReportsView.vue'
import RunsView from './views/RunsView.vue'
import ScheduledJobsView from './views/ScheduledJobsView.vue'
import SettingsView from './views/SettingsView.vue'
import WorkbenchView from './views/WorkbenchView.vue'

export const router = createRouter({
  history: createWebHashHistory(import.meta.env.BASE_URL),
  routes: [
    { path: '/', name: 'workbench', component: WorkbenchView },
    { path: '/assets', name: 'assets', component: AssetsView },
    { path: '/baselines', name: 'baselines', component: BaselinesView },
    { path: '/scheduled-jobs', name: 'scheduled-jobs', component: ScheduledJobsView },
    { path: '/runs', name: 'runs', component: RunsView },
    { path: '/reports', name: 'reports', component: ReportsView },
    { path: '/settings', name: 'settings', component: SettingsView },
  ],
})
