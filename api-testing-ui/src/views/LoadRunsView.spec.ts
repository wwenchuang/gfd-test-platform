// @vitest-environment jsdom

import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { createMemoryHistory, createRouter } from 'vue-router'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { useContextStore } from '../stores/context'
import { useLoadTestingStore } from '../stores/loadTesting'
import LoadRunsView from './LoadRunsView.vue'

describe('LoadRunsView', () => {
  beforeEach(() => { setActivePinia(createPinia()); vi.restoreAllMocks() })
  it('creates a draft then exposes connectivity, preflight, start, stop and report actions in order', async () => {
    const context = useContextStore(); Object.assign(context, { projectId: 'p1', environmentRevisions: [{ id: 'env1', environment_id: 'e', project_id: 'p1', name: '性能环境', revision: 1 }] })
    vi.spyOn(context, 'loadSavedContext').mockResolvedValue(); vi.spyOn(context, 'loadOptions').mockResolvedValue()
    const store = useLoadTestingStore()
    store.scenarios = [{ id: 's1', project_id: 'p1', name: '核心链路', description: '', scenario_type: 'workflow', active_version_id: 'v1', status: 'active', created_at: '', updated_at: '' }]
    store.agents = []
    vi.spyOn(store, 'loadScenarios').mockResolvedValue(store.scenarios); vi.spyOn(store, 'loadAgents').mockResolvedValue([]); vi.spyOn(store, 'loadRuns').mockResolvedValue([])
    const run = { id: 'r1', project_id: 'p1', scenario_version_id: 'v1', environment_revision_id: 'env1', load_model: 'constant-vus' as const, queue_priority: 'normal' as const, configuration: { scenario: { name: '核心链路' }, agents: [{ id: 'a1', name: '专用节点' }] }, state: 'draft' as const, verdict: null, stop_reason: '', ai_analysis_state: 'pending', summary: {}, created_at: '', started_at: null, finished_at: null, updated_at: '' }
    const readyAgent = { id: 'a1', name: '专用节点', status: 'online', scheduling_tier: 'preferred' as const, node_group: '', labels: {}, agent_version: '1', k6_version: '1', hard_limits: {}, soft_limits: {}, current_usage: {}, health: { target_connectivity: { env1: { reachable: true, message: '通过' } } }, calibration_state: 'valid' as const, egress_ip: '', last_heartbeat_at: '', offline_reason: '' }
    vi.spyOn(store, 'createRun').mockResolvedValue(run)
    vi.spyOn(store, 'prepareConnectivity').mockImplementation(async () => { store.agents = [readyAgent as never]; return store.agents })
    vi.spyOn(store, 'preflightRun').mockImplementation(async () => { store.runs = [{ ...run, state: 'queued' }]; return store.runs[0] })
    vi.spyOn(store, 'startRun').mockImplementation(async () => { store.runs = [{ ...run, state: 'starting' }]; return store.runs[0] })
    vi.spyOn(store, 'stopRun').mockResolvedValue({ ...run, state: 'cancelled' })
    const router = createRouter({ history: createMemoryHistory(), routes: [{ path: '/', name: 'load-runs', component: LoadRunsView }, { path: '/reports', name: 'load-reports', component: { template: '<div />' } }] })
    const wrapper = mount(LoadRunsView, { global: { plugins: [router] } })
    await flushPromises()
    await wrapper.get('[data-testid="load-run-new"]').trigger('click')
    expect(wrapper.text()).toContain('没有可用的已校准节点')
    await wrapper.find('.load-wizard .secondary-command').trigger('click')
    store.agents = []
    store.runs = [run]
    await wrapper.vm.$nextTick()
    expect(wrapper.get('[data-testid="run-preflight-r1"]').attributes('disabled')).toBeDefined()
    await wrapper.get('[data-testid="run-connectivity-r1"]').trigger('click')
    await flushPromises()
    expect(wrapper.get('[data-testid="connectivity-r1-a1"]').text()).toContain('连通性通过')
    expect(wrapper.get('[data-testid="run-preflight-r1"]').attributes('disabled')).toBeUndefined()
    await wrapper.get('[data-testid="run-preflight-r1"]').trigger('click')
    await wrapper.vm.$nextTick()
    await wrapper.get('[data-testid="run-start-r1"]').trigger('click')
    await wrapper.vm.$nextTick()
    await wrapper.get('[data-testid="run-stop-r1"]').trigger('click')
    expect(store.prepareConnectivity).toHaveBeenCalledWith('r1')
    expect(store.preflightRun).toHaveBeenCalledWith('r1')
    expect(store.startRun).toHaveBeenCalledWith('r1')
    expect(store.stopRun).toHaveBeenCalledWith('r1')
  })
})
