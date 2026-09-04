// @vitest-environment jsdom

import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { createMemoryHistory, createRouter } from 'vue-router'
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
    const router = createRouter({ history: createMemoryHistory(), routes: [{ path: '/', name: 'load-scenarios', component: LoadScenariosView }, { path: '/runs', name: 'load-runs', component: { template: '<div />' } }] }); await router.push('/'); await router.isReady()
    const wrapper = mount(LoadScenariosView, { global: { plugins: [router] } })
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

  it('shows application context and supports creating a new immutable version or archiving', async () => {
    const context = useContextStore(); Object.assign(context, { projectId: 'p1', sourceRevisionId: 'src1', projects: [{ id: 'p1', name: '智小白3D家用' }] })
    vi.spyOn(context, 'loadSavedContext').mockResolvedValue(); vi.spyOn(context, 'loadOptions').mockResolvedValue()
    const assets = useAssetsStore(); assets.endpoints = [{ id: 'e1', method: 'GET', path: '/search', summary: '搜索', tags: [] }]; vi.spyOn(assets, 'load').mockResolvedValue()
    const store = useLoadTestingStore(); store.scenarios = [{ id: 's1', project_id: 'p1', name: '模型搜索', description: '说明', scenario_type: 'single_interface', active_version_id: 'v1', status: 'active', created_at: '', updated_at: '' }]
    vi.spyOn(store, 'loadScenarios').mockResolvedValue(store.scenarios)
    vi.spyOn(store, 'loadScenarioVersion').mockResolvedValue({ id: 'v1', scenario_id: 's1', version_number: 1, definition: { name: '模型搜索', description: '说明', mode: 'single_interface', steps: [], dataset_contract: { dataset_id: null, usage_mode: 'cycle', variables: [] }, risk: { level: 'low', ownership_variable: null, notes: '' }, source_snapshot: {} }, validation_summary: {}, preflight_summary: {}, compiler_version: '1', content_hash: 'x', created_at: '' })
    const archive = vi.spyOn(store, 'archiveScenario').mockResolvedValue()
    vi.spyOn(window, 'confirm').mockReturnValue(true)
    const router = createRouter({ history: createMemoryHistory(), routes: [{ path: '/', name: 'load-scenarios', component: LoadScenariosView }, { path: '/runs', name: 'load-runs', component: { template: '<div />' } }] }); await router.push('/'); await router.isReady()
    const wrapper = mount(LoadScenariosView, { global: { plugins: [router] } })
    await flushPromises()
    expect(wrapper.text()).toContain('所属应用 / API 项目')
    expect(wrapper.text()).toContain('智小白3D家用')
    await wrapper.get('[data-testid="scenario-edit-s1"]').trigger('click')
    await flushPromises()
    expect(wrapper.text()).toContain('创建新版本')
    await wrapper.get('[data-testid="scenario-cancel"]').trigger('click')
    await wrapper.get('[data-testid="scenario-archive-s1"]').trigger('click')
    expect(archive).toHaveBeenCalledWith('s1')
  })

  it('starts a new load run from the selected scenario without making the user find it again', async () => {
    const context = useContextStore(); Object.assign(context, { projectId: 'p1', sourceRevisionId: 'src1', projects: [{ id: 'p1', name: '智小白3D家用' }] })
    vi.spyOn(context, 'loadSavedContext').mockResolvedValue(); vi.spyOn(context, 'loadOptions').mockResolvedValue()
    const assets = useAssetsStore(); assets.endpoints = []; vi.spyOn(assets, 'load').mockResolvedValue()
    const store = useLoadTestingStore(); store.scenarios = [{ id: 's1', project_id: 'p1', name: '模型搜索', description: '说明', scenario_type: 'single_interface', active_version_id: 'v1', status: 'active', created_at: '', updated_at: '' }]
    vi.spyOn(store, 'loadScenarios').mockResolvedValue(store.scenarios)
    const router = createRouter({ history: createMemoryHistory(), routes: [{ path: '/', name: 'load-scenarios', component: LoadScenariosView }, { path: '/runs', name: 'load-runs', component: { template: '<div />' } }] })
    await router.push('/'); await router.isReady()
    const wrapper = mount(LoadScenariosView, { global: { plugins: [router] } })
    await flushPromises()

    await wrapper.get('[data-testid="scenario-run-s1"]').trigger('click')
    await flushPromises()
    expect(router.currentRoute.value.name).toBe('load-runs')
    expect(router.currentRoute.value.query.scenario_id).toBe('s1')
  })
})
