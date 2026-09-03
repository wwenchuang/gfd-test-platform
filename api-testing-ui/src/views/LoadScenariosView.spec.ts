// @vitest-environment jsdom

import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { useAssetsStore } from '../stores/assets'
import { useContextStore } from '../stores/context'
import { useLoadTestingStore } from '../stores/loadTesting'
import LoadScenariosView from './LoadScenariosView.vue'

describe('LoadScenariosView', () => {
  beforeEach(() => { setActivePinia(createPinia()); vi.restoreAllMocks() })
  it('opens, cancels and saves the scenario wizard while showing backend rejection', async () => {
    const context = useContextStore(); Object.assign(context, { projectId: 'p1', sourceRevisionId: 'src1', projects: [{ id: 'p1', name: '3D家用' }] })
    vi.spyOn(context, 'loadSavedContext').mockResolvedValue(); vi.spyOn(context, 'loadOptions').mockResolvedValue()
    const assets = useAssetsStore(); assets.endpoints = [{ id: 'e1', method: 'GET', path: '/search', summary: '搜索', tags: [] }]; vi.spyOn(assets, 'load').mockResolvedValue()
    const store = useLoadTestingStore(); vi.spyOn(store, 'loadScenarios').mockResolvedValue([])
    const wrapper = mount(LoadScenariosView)
    await flushPromises()
    expect(wrapper.text()).toContain('还没有性能场景')
    await wrapper.get('[data-testid="load-scenario-new"]').trigger('click')
    expect(wrapper.findComponent({ name: 'LoadScenarioWizard' }).exists()).toBe(true)
    await wrapper.get('[data-testid="scenario-cancel"]').trigger('click')
    expect(wrapper.findComponent({ name: 'LoadScenarioWizard' }).exists()).toBe(false)
    store.scenarioError = '写接口缺少清理步骤'
    await wrapper.get('[data-testid="load-scenario-new"]').trigger('click')
    expect(wrapper.get('[role="alert"]').text()).toContain('写接口缺少清理步骤')
  })
})
