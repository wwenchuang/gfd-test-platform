// @vitest-environment jsdom

import { mount } from '@vue/test-utils'
import { createMemoryHistory, createRouter } from 'vue-router'
import { describe, expect, it } from 'vitest'

import App from './App.vue'
import { router as appRouter } from './router'
import WorkbenchView from './views/WorkbenchView.vue'

describe('App navigation', () => {
  it('exposes a dedicated case management entry', async () => {
    const StubView = { template: '<div />' }
    const router = createRouter({
      history: createMemoryHistory(),
      routes: [
        { path: '/', name: 'workbench', component: WorkbenchView },
        { path: '/cases', name: 'cases', component: WorkbenchView },
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
    const link = wrapper.get('[data-testid="nav-cases"]')

    expect(link.text()).toContain('用例管理')
    expect(link.attributes('href')).toBe('/cases')
  })

  it('registers a dedicated case management route', () => {
    expect(appRouter.getRoutes()).toEqual(expect.arrayContaining([
      expect.objectContaining({ path: '/cases', name: 'cases' }),
    ]))
  })
})
