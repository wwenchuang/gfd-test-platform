// @vitest-environment jsdom

import { mount } from '@vue/test-utils'
import { createMemoryHistory, createRouter } from 'vue-router'
import { describe, expect, it } from 'vitest'

import App from './App.vue'
import { router as appRouter } from './router'
import CasesView from './views/CasesView.vue'
import TasksView from './views/TasksView.vue'
import WorkbenchView from './views/WorkbenchView.vue'

describe('App navigation', () => {
  it('exposes dedicated task and case management entries', async () => {
    const StubView = { template: '<div />' }
    const router = createRouter({
      history: createMemoryHistory(),
      routes: [
        { path: '/', name: 'workbench', component: WorkbenchView },
        { path: '/tasks', name: 'tasks', component: TasksView },
        { path: '/cases', name: 'cases', component: CasesView },
        { path: '/assets', name: 'assets', component: StubView },
        { path: '/baselines', name: 'baselines', component: StubView },
        { path: '/scheduled-jobs', name: 'scheduled-jobs', component: StubView },
        { path: '/runs', name: 'runs', component: StubView },
        { path: '/reports', name: 'reports', component: StubView },
        { path: '/settings', name: 'settings', component: StubView },
      ],
    })
    await router.push('/')
    await router.isReady()

    const wrapper = mount(App, { global: { plugins: [router], stubs: { RouterView: true } } })
    const taskLink = wrapper.get('[data-testid="nav-tasks"]')
    const link = wrapper.get('[data-testid="nav-cases"]')

    expect(taskLink.text()).toContain('任务管理')
    expect(taskLink.attributes('href')).toBe('/tasks')
    expect(link.text()).toContain('用例管理')
    expect(link.attributes('href')).toBe('/cases')
  })

  it('registers dedicated management routes with independent view components', () => {
    expect(appRouter.getRoutes()).toEqual(expect.arrayContaining([
      expect.objectContaining({ path: '/tasks', name: 'tasks', components: expect.objectContaining({ default: TasksView }) }),
      expect.objectContaining({ path: '/cases', name: 'cases', components: expect.objectContaining({ default: CasesView }) }),
    ]))
  })
})
