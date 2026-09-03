// @vitest-environment jsdom

import { flushPromises, mount } from '@vue/test-utils'
import { createMemoryHistory, createRouter } from 'vue-router'
import { nextTick } from 'vue'
import { beforeEach, describe, expect, it } from 'vitest'

import App from './App.vue'
import { router as appRouter } from './router'
import CasesView from './views/CasesView.vue'
import TasksView from './views/TasksView.vue'
import WorkbenchView from './views/WorkbenchView.vue'
import { setApiTestingAccessProfile } from './utils/authRedirect'

describe('App navigation', () => {
  beforeEach(() => setApiTestingAccessProfile(null))
  it('groups management pages by the API testing workflow', async () => {
    const StubView = { template: '<div />' }
    const router = createRouter({
      history: createMemoryHistory(),
      routes: [
        { path: '/', component: StubView },
        { path: '/tasks', component: StubView },
        { path: '/cases', component: StubView },
        { path: '/assets', component: StubView },
        { path: '/baselines', component: StubView },
        { path: '/scheduled-jobs', component: StubView },
        { path: '/runs', component: StubView },
        { path: '/reports', component: StubView },
        { path: '/settings', component: StubView },
      ],
    })
    await router.push('/')
    await router.isReady()

    const wrapper = mount(App, { global: { plugins: [router], stubs: { RouterView: true } } })
    const sections = wrapper.findAll('[data-testid^="nav-section-"]')

    expect(sections.map(section => section.get('.rail-section-label').text())).toEqual([
      '设计准备',
      '性能测试',
      '回归编排',
      '结果分析',
      '项目配置',
    ])
    expect(wrapper.get('[data-testid="nav-section-design"]').text()).toContain('工作台')
    expect(wrapper.get('[data-testid="nav-section-design"]').text()).toContain('接口资产')
    expect(wrapper.get('[data-testid="nav-section-design"]').text()).toContain('用例管理')
    expect(wrapper.get('[data-testid="nav-section-regression"]').text()).toContain('任务管理')
    expect(wrapper.get('[data-testid="nav-section-results"]').text()).toContain('测试报告')
    expect(wrapper.get('[data-testid="nav-section-settings"]').text()).toContain('环境配置')
  })

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

  it('shows the performance group only with load-test permission and registers lazy routes', async () => {
    const StubView = { template: '<div />' }
    const router = createRouter({ history: createMemoryHistory(), routes: [
      { path: '/', component: StubView },
      { path: '/load-scenarios', component: StubView },
      { path: '/load-runs', component: StubView },
      { path: '/load-reports', component: StubView },
      { path: '/load-agents', component: StubView },
    ] })
    await router.push('/')
    await router.isReady()
    setApiTestingAccessProfile({ status: 'active', permissions: ['api.view'] })
    const hidden = mount(App, { global: { plugins: [router], stubs: { RouterView: true } } })
    expect(hidden.find('[data-testid="nav-section-load-testing"]').exists()).toBe(false)
    hidden.unmount()

    setApiTestingAccessProfile({ status: 'active', permissions: ['api.view', 'api.loadtest.view'] })
    const visible = mount(App, { global: { plugins: [router], stubs: { RouterView: true } } })
    const section = visible.get('[data-testid="nav-section-load-testing"]')
    expect(section.text()).toContain('性能场景')
    expect(section.text()).toContain('压测执行')
    expect(section.text()).toContain('性能报告')
    expect(section.text()).toContain('压测节点')
    visible.unmount()

    const paths = appRouter.getRoutes().map(route => route.path)
    expect(paths).toEqual(expect.arrayContaining(['/load-scenarios', '/load-runs', '/load-reports', '/load-agents']))
  })

  it('opens a labeled navigation drawer on narrow screens and closes it after navigation', async () => {
    const StubView = { template: '<div />' }
    const router = createRouter({
      history: createMemoryHistory(),
      routes: [
        { path: '/', component: StubView },
        { path: '/cases', component: StubView },
        { path: '/tasks', component: StubView },
        { path: '/assets', component: StubView },
        { path: '/baselines', component: StubView },
        { path: '/scheduled-jobs', component: StubView },
        { path: '/runs', component: StubView },
        { path: '/reports', component: StubView },
        { path: '/settings', component: StubView },
      ],
    })
    await router.push('/')
    await router.isReady()

    const wrapper = mount(App, { attachTo: document.body, global: { plugins: [router], stubs: { RouterView: true } } })
    const toggle = wrapper.get('[data-testid="mobile-nav-toggle"]')

    expect(toggle.attributes('aria-expanded')).toBe('false')
    expect(wrapper.get('.side-rail').classes()).not.toContain('mobile-open')

    await toggle.trigger('click')
    await nextTick()
    expect(toggle.attributes('aria-expanded')).toBe('true')
    expect(wrapper.get('.side-rail').classes()).toContain('mobile-open')
    expect(wrapper.get('.side-rail').text()).toContain('用例管理')
    expect(document.activeElement).toBe(wrapper.get('.mobile-nav-close').element)

    await wrapper.get('.side-rail').trigger('keydown', { key: 'Escape' })
    await nextTick()
    expect(wrapper.get('[data-testid="mobile-nav-toggle"]').attributes('aria-expanded')).toBe('false')
    expect(document.activeElement).toBe(wrapper.get('[data-testid="mobile-nav-toggle"]').element)

    await wrapper.get('[data-testid="mobile-nav-toggle"]').trigger('click')

    await wrapper.get('[data-testid="nav-cases"]').trigger('click')
    await flushPromises()
    expect(router.currentRoute.value.path).toBe('/cases')
    expect(wrapper.get('[data-testid="mobile-nav-toggle"]').attributes('aria-expanded')).toBe('false')
    expect(wrapper.get('.side-rail').classes()).not.toContain('mobile-open')
    wrapper.unmount()
  })
})
