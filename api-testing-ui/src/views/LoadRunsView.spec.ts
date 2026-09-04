// @vitest-environment jsdom

import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { createMemoryHistory, createRouter } from 'vue-router'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { useContextStore } from '../stores/context'
import { useLoadTestingStore } from '../stores/loadTesting'
import LoadRunsView from './LoadRunsView.vue'

describe('LoadRunsView', () => {
  beforeEach(() => { setActivePinia(createPinia()); vi.restoreAllMocks() })
  afterEach(() => vi.useRealTimers())
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
    await wrapper.get('[data-testid="load-run-back"]').trigger('click')
    store.agents = []
    store.runs = [run]
    await wrapper.vm.$nextTick()
    expect(wrapper.find('[data-testid="run-preflight-r1"]').exists()).toBe(false)
    await wrapper.get('[data-testid="run-connectivity-r1"]').trigger('click')
    await vi.waitFor(() => expect(wrapper.get('[data-testid="connectivity-r1-a1"]').text()).toContain('连通性通过'))
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

  it('refreshes active runs automatically and deletes terminal history from the page', async () => {
    vi.useFakeTimers()
    const context = useContextStore(); Object.assign(context, { projectId: 'p1', projects: [{ id: 'p1', name: '3D家用' }], environmentRevisions: [] })
    vi.spyOn(context, 'loadSavedContext').mockResolvedValue(); vi.spyOn(context, 'loadOptions').mockResolvedValue()
    const store = useLoadTestingStore()
    const run = { id: 'r1', project_id: 'p1', scenario_version_id: 'v1', environment_revision_id: 'env1', load_model: 'constant-vus' as const, queue_priority: 'normal' as const, configuration: { scenario: { id: 's1', name: '核心链路' }, agents: [] }, state: 'finished' as const, verdict: 'passed' as const, stop_reason: '', ai_analysis_state: 'pending', summary: {}, created_at: '2026-09-04T12:00:00Z', started_at: '2026-09-04T12:00:01Z', finished_at: '2026-09-04T12:00:10Z', updated_at: '' }
    const scenario = { id: 's1', project_id: 'p1', name: '核心链路', description: '', scenario_type: 'workflow' as const, active_version_id: 'v1', status: 'active', created_at: '', updated_at: '' }
    store.runs = [run]; store.scenarios = [scenario]
    vi.spyOn(store, 'loadScenarios').mockResolvedValue([scenario]); vi.spyOn(store, 'loadAgents').mockResolvedValue([])
    const loadRuns = vi.spyOn(store, 'loadRuns').mockResolvedValue(store.runs)
    const remove = vi.spyOn(store, 'deleteRun').mockResolvedValue()
    vi.spyOn(window, 'confirm').mockReturnValue(true)
    const router = createRouter({ history: createMemoryHistory(), routes: [{ path: '/', name: 'load-runs', component: LoadRunsView }, { path: '/reports', name: 'load-reports', component: { template: '<div />' } }] })
    const wrapper = mount(LoadRunsView, { global: { plugins: [router] } })
    await flushPromises()
    expect(wrapper.text()).toContain('自动刷新')
    expect(wrapper.text()).toContain('3D家用')
    expect(wrapper.get('[data-testid="run-stage-r1-preflight"]').text()).toContain('✓')
    expect(wrapper.find('[data-testid="run-connectivity-r1"]').exists()).toBe(false)
    expect(wrapper.find('[data-testid="run-preflight-r1"]').exists()).toBe(false)
    expect(wrapper.get('[data-testid="run-rerun-r1"]').text()).toContain('再次压测')
    await vi.advanceTimersByTimeAsync(3000)
    expect(loadRuns).toHaveBeenLastCalledWith('p1', true)
    await wrapper.get('[data-testid="run-delete-r1"]').trigger('click')
    expect(remove).toHaveBeenCalledWith('r1')
    wrapper.unmount()
  })

  it('does not show a success check when the preflight returns a failed run', async () => {
    const context = useContextStore(); Object.assign(context, { projectId: 'p1', projects: [{ id: 'p1', name: '3D家用' }], environmentRevisions: [] })
    vi.spyOn(context, 'loadSavedContext').mockResolvedValue(); vi.spyOn(context, 'loadOptions').mockResolvedValue()
    const store = useLoadTestingStore()
    const run = { id: 'r1', project_id: 'p1', scenario_version_id: 'v1', environment_revision_id: 'env1', load_model: 'constant-vus' as const, queue_priority: 'normal' as const, configuration: { scenario: { id: 's1', name: '核心链路' }, agents: [{ id: 'a1', name: '专用节点' }] }, state: 'draft' as const, verdict: null, stop_reason: '', ai_analysis_state: 'pending', summary: {}, created_at: '', started_at: null, finished_at: null, updated_at: '' }
    const readyAgent = { id: 'a1', name: '专用节点', status: 'online', scheduling_tier: 'preferred' as const, node_group: '', labels: {}, agent_version: '1', k6_version: '1', hard_limits: {}, soft_limits: {}, current_usage: {}, health: { target_connectivity: { env1: { reachable: true, message: '通过' } } }, calibration_state: 'valid' as const, egress_ip: '', last_heartbeat_at: '', offline_reason: '' }
    store.runs = [run]; store.agents = [readyAgent as never]
    vi.spyOn(store, 'loadScenarios').mockResolvedValue([]); vi.spyOn(store, 'loadAgents').mockResolvedValue(store.agents); vi.spyOn(store, 'loadRuns').mockResolvedValue(store.runs)
    vi.spyOn(store, 'preflightRun').mockImplementation(async () => {
      const failed = { ...run, state: 'failed' as const, summary: { preflight: { passed: false, message: '业务断言失败' } } }
      store.runs = [failed]
      return failed
    })
    const router = createRouter({ history: createMemoryHistory(), routes: [{ path: '/', name: 'load-runs', component: LoadRunsView }, { path: '/reports', name: 'load-reports', component: { template: '<div />' } }] })
    const wrapper = mount(LoadRunsView, { global: { plugins: [router] } })
    await flushPromises()
    await wrapper.get('[data-testid="run-preflight-r1"]').trigger('click')
    expect(wrapper.text()).toContain('预检未通过：业务断言失败')
    expect(wrapper.text()).not.toContain('✅ 业务断言失败')
    wrapper.unmount()
  })
})
