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
})
