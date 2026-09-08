// @vitest-environment jsdom

import { mount, flushPromises } from '@vue/test-utils'
import { afterEach, describe, expect, it, vi } from 'vitest'
import LoadRunWizard from './LoadRunWizard.vue'
import { setApiTestingAccessProfile } from '../utils/authRedirect'

vi.mock('../api/monitoring', () => ({ monitoringApi: { list: vi.fn().mockResolvedValue([{ id: 'monitor', revision_id: 'monitor-v1', status: 'active', name: '目标主机', labels: { instance: 'host:9100' }, metrics: ['cpu_percent'] }]) } }))

const scenario = { id: 's1', project_id: 'p1', name: '搜索链路', description: '', scenario_type: 'single_interface' as const, active_version_id: 'v1', status: 'active', created_at: '', updated_at: '' }
const environments = [{ id: 'env-v1', environment_id: 'env', project_id: 'p1', name: '性能测试环境', revision: 1 }]
const agents = [{ id: 'a1', name: '专用节点', status: 'online', scheduling_tier: 'preferred' as const, node_group: '上海', labels: {}, agent_version: '1', k6_version: 'k6', hard_limits: { max_processes: 1, max_vus: 500, max_iterations_per_second: 2000, max_duration_seconds: 1800, cpu_cores: 8, memory_mb: 16384 }, soft_limits: { max_processes: 1, max_vus: 400, max_iterations_per_second: 1500, max_duration_seconds: 1200, cpu_cores: 8, memory_mb: 12000 }, current_usage: { processes: 0, vus: 0 }, health: { calibration: { state: 'valid', max_vus: 320, max_iterations_per_second: 1200, valid_until: '2099-01-01' } }, calibration_state: 'valid' as const, egress_ip: '', last_heartbeat_at: '', offline_reason: '' }]

describe('LoadRunWizard', () => {
  afterEach(() => setApiTestingAccessProfile(null))
  it('submits frozen monitoring revisions and validates observation windows', async () => {
    const wrapper = mount(LoadRunWizard, { props: { scenario, environments, agents } })
    await flushPromises()
    await wrapper.get('[data-testid="load-agent-a1"]').setValue(true)
    await wrapper.get('[data-testid="monitor-select-monitor"]').setValue(true)
    await wrapper.get('[data-testid="monitor-required-monitor"]').setValue(true)
    await wrapper.get('[data-testid="monitor-after"]').setValue('-1')
    expect(wrapper.get('[data-testid="load-run-submit"]').attributes('disabled')).toBeDefined()
    await wrapper.get('[data-testid="monitor-after"]').setValue('0')
    await wrapper.get('[data-testid="load-run-submit"]').trigger('click')
    expect(wrapper.emitted('submit')?.[0]?.[0]).toMatchObject({ monitoring: { services: [{ revision_id: 'monitor-v1', required: true }], before_seconds: 60, after_seconds: 0 } })
  })

  it('does not pretend VU multiplied by seconds is an exact iteration count', async () => {
    const wrapper = mount(LoadRunWizard, { props: { scenario, environments, agents, projectName: '智小白3D家用', initialEnvironmentId: 'env-v1' } })
    await wrapper.get('[data-testid="load-model-constant-vus"]').trigger('click')
    expect(wrapper.text()).toContain('所属应用 / API 项目')
    expect(wrapper.text()).toContain('智小白3D家用')
    expect(wrapper.text()).toContain('可切换')
    expect(wrapper.text()).toContain('固定并发会在时长内持续循环')
    expect(wrapper.text()).toContain('实际次数取决于接口响应时间')
    expect(wrapper.text()).not.toContain('预计约 1200 次完整链路')
  })

  it('offers a clear return action and recommends enough nodes after workload is known', async () => {
    const wrapper = mount(LoadRunWizard, { props: { scenario, environments, agents, projectName: '智小白3D家用' } })
    expect(wrapper.get('[data-testid="load-run-back"]').text()).toContain('返回执行列表')
    await wrapper.get('[data-testid="load-model-constant-arrival-rate"]').trigger('click')
    await wrapper.get('[data-testid="load-agent-recommend"]').trigger('click')
    expect((wrapper.get('[data-testid="load-agent-a1"]').element as HTMLInputElement).checked).toBe(true)
    expect(wrapper.text()).toContain('选择依据')
  })

  it('explains all four load models and emits target, thresholds, allocation and priority', async () => {
    const wrapper = mount(LoadRunWizard, { props: { scenario, environments, agents } })
    expect(wrapper.text()).toContain('固定并发')
    expect(wrapper.text()).toContain('阶梯并发')
    expect(wrapper.text()).toContain('固定吞吐')
    expect(wrapper.text()).toContain('阶梯吞吐')
    await wrapper.get('[data-testid="load-model-constant-arrival-rate"]').trigger('click')
    await wrapper.get('[data-testid="load-rate"]').setValue('100')
    await wrapper.get('[data-testid="load-max-vus"]').setValue('40')
    await wrapper.get('[data-testid="load-duration"]').setValue('60')
    await wrapper.get('[data-testid="load-p95"]').setValue('500')
    await wrapper.get('[data-testid="load-agent-a1"]').setValue(true)
    expect(wrapper.text()).toContain('预计约 6000 次完整链路')
    expect(wrapper.text()).toContain('当前可用 320 VU / 1200 次/秒')
    await wrapper.get('[data-testid="load-run-submit"]').trigger('click')
    expect(wrapper.emitted('submit')?.[0]?.[0]).toMatchObject({
      scenario_version_id: 'v1', environment_revision_id: 'env-v1',
      workload: { executor: 'constant-arrival-rate', rate: 100, time_unit: '1s', duration_seconds: 60, pre_allocated_vus: 1, max_vus: 40 },
      thresholds: { p95_ms: { operator: 'less_than_or_equal', value: 500, required: true } },
      priority: 'normal', allocation_policy: { agent_ids: ['a1'], allow_fallback: false },
    })
  })

  it.each([
    ['constant-vus', { executor: 'constant-vus', vus: 1, duration_seconds: 10 }],
    ['ramping-vus', { executor: 'ramping-vus', start_vus: 1, stages: [{ duration_seconds: 10, target: 1 }] }],
    ['ramping-arrival-rate', { executor: 'ramping-arrival-rate', start_rate: 1, time_unit: '1s', pre_allocated_vus: 1, max_vus: 1, stages: [{ duration_seconds: 10, target: 1 }] }],
  ])('emits the exact backend workload contract for %s', async (model, workload) => {
    const wrapper = mount(LoadRunWizard, { props: { scenario, environments, agents } })
    await wrapper.get(`[data-testid="load-model-${model}"]`).trigger('click')
    await wrapper.get('[data-testid="load-agent-a1"]').setValue(true)
    await wrapper.get('[data-testid="load-run-submit"]').trigger('click')
    expect(wrapper.emitted('submit')?.[0]?.[0]).toMatchObject({ workload })
  })

  it('blocks a capacity shortfall unless the user explicitly accepts inconclusive evidence', async () => {
    const wrapper = mount(LoadRunWizard, { props: { scenario, environments, agents } })
    await wrapper.get('[data-testid="load-model-constant-arrival-rate"]').trigger('click')
    await wrapper.get('[data-testid="load-rate"]').setValue('1300')
    await wrapper.get('[data-testid="load-agent-a1"]').setValue(true)
    expect(wrapper.get('[data-testid="capacity-shortfall"]').text()).toContain('容量不足')
    expect(wrapper.get('[data-testid="load-run-submit"]').attributes('disabled')).toBeDefined()
    await wrapper.get('[data-testid="allow-run-anyway"]').setValue(true)
    expect(wrapper.get('[data-testid="load-run-submit"]').attributes('disabled')).toBeUndefined()
  })

  it('checks arrival-rate capacity against the visible maximum VU instead of a hidden 100 VU floor', async () => {
    const wrapper = mount(LoadRunWizard, { props: { scenario, environments, agents } })
    await wrapper.get('[data-testid="load-model-constant-arrival-rate"]').trigger('click')
    await wrapper.get('[data-testid="load-rate"]').setValue('1')
    await wrapper.get('[data-testid="load-vus"]').setValue('1')
    await wrapper.get('[data-testid="load-max-vus"]').setValue('400')
    await wrapper.get('[data-testid="load-agent-a1"]').setValue(true)
    expect(wrapper.get('[data-testid="capacity-shortfall"]').text()).toContain('最大并发需要 400 VU')
    expect(wrapper.get('[data-testid="load-run-submit"]').attributes('disabled')).toBeDefined()
  })

  it('blocks uncalibrated selection and requires production confirmation', async () => {
    const blocked = { ...agents[0], calibration_state: 'expired' as const }
    const production = [{ ...environments[0], name: '生产环境' }]
    const wrapper = mount(LoadRunWizard, { props: { scenario, environments: production, agents: [blocked] } })
    expect(wrapper.text()).toContain('校准过期，不能选择')
    expect(wrapper.get('[data-testid="load-agent-a1"]').attributes('disabled')).toBeDefined()
    expect(wrapper.get('[data-testid="load-run-submit"]').attributes('disabled')).toBeDefined()
    expect(wrapper.text()).toContain('生产环境会持续收到真实请求')
  })

  it('explains and blocks production runs when api.production is missing', () => {
    setApiTestingAccessProfile({ permissions: ['api.loadtest.execute'] })
    const production = [{ ...environments[0], name: '生产环境' }]
    const wrapper = mount(LoadRunWizard, { props: { scenario, environments: production, agents } })
    expect(wrapper.text()).toContain('当前账号没有 api.production 权限')
    expect(wrapper.get('[data-testid="load-run-submit"]').attributes('disabled')).toBeDefined()
  })
})

it('applies editable business acceptance criteria and blocks invalid percentages', async()=>{
 const wrapper=mount(LoadRunWizard,{props:{scenario,environments,agents}})
 await flushPromises();await wrapper.get('[data-testid="load-agent-a1"]').setValue(true)
 await wrapper.get('[data-testid="threshold-business-enabled"]').setValue(true)
 await wrapper.get('[data-testid="threshold-business"]').setValue('2')
 await wrapper.get('[data-testid="threshold-http"]').setValue('101')
 expect(wrapper.get('[data-testid="load-run-submit"]').attributes('disabled')).toBeDefined()
 await wrapper.get('[data-testid="threshold-http"]').setValue('0.5')
 await wrapper.get('[data-testid="load-run-submit"]').trigger('click')
 expect(wrapper.emitted('submit')?.[0]?.[0]).toMatchObject({test_context:{purpose:'smoke'},thresholds:{http_error_rate:{value:.005},workflow_failure_rate:{value:0},business_failure_rate:{value:.02}}})
})

it('starts smoke runs with a bounded low arrival rate', async () => {
  const wrapper = mount(LoadRunWizard, { props: { scenario, environments, agents } })
  await wrapper.get('[data-testid="load-agent-a1"]').setValue(true)
  await wrapper.get('[data-testid="load-run-submit"]').trigger('click')
  expect(wrapper.emitted('submit')?.[0]?.[0]).toMatchObject({ workload: { executor: 'constant-arrival-rate', rate: 1, duration_seconds: 10, pre_allocated_vus: 1, max_vus: 1 } })
})

it('never recommends disabled nodes and invalidates a selected node when its state changes', async () => {
  const wrapper = mount(LoadRunWizard, { props: { scenario, environments, agents } })
  await wrapper.get('[data-testid="load-agent-a1"]').setValue(true)
  await wrapper.setProps({ agents: [{ ...agents[0], scheduling_tier: 'disabled' as const }] })
  expect(wrapper.get('[data-testid="load-agent-a1"]').attributes('disabled')).toBeDefined()
  expect(wrapper.get('[data-testid="load-run-submit"]').attributes('disabled')).toBeDefined()
  expect(wrapper.text()).toContain('已停用')
  expect(wrapper.get('[data-testid="load-agent-recommend"]').attributes('disabled')).toBeDefined()
})

it('requires enough total rate and VU for every selected node without raising them silently', async () => {
  const wrapper=mount(LoadRunWizard,{props:{scenario,environments,agents:[...agents,{...agents[0],id:'a2',scheduling_tier:'normal' as const}]}})
  await wrapper.get('[data-testid="load-agent-a1"]').setValue(true)
  await wrapper.get('[data-testid="load-agent-a2"]').setValue(true)
  await wrapper.get('[data-testid="load-distribution-all"]').setValue(true)
  expect(wrapper.get('[data-testid="load-run-submit"]').attributes('disabled')).toBeDefined()
  await wrapper.get('[data-testid="load-rate"]').setValue(2)
  await wrapper.get('[data-testid="load-vus"]').setValue(2)
  await wrapper.get('[data-testid="load-max-vus"]').setValue(2)
  await wrapper.get('[data-testid="load-run-submit"]').trigger('click')
  expect(wrapper.emitted('submit')?.[0]?.[0]).toMatchObject({workload:{rate:2,max_vus:2},allocation_policy:{distribution:'all_selected',agent_ids:['a1','a2']}})
})

it('prefills next run without dropping original thresholds or monitoring and never auto submits', async () => {
  const preset = {sourceId:'r',scenarioId:'s1',scenarioVersionId:'old-v',environmentId:'env-v1',executor:'constant-arrival-rate' as const,target:2,timeUnit:'1s' as const,duration:60,maxVus:3,thresholds:{p99_ms:{operator:'less_than',value:777,required:false}},monitoring:{services:[{revision_id:'old-monitor',required:true}],before_seconds:30,after_seconds:90},previous:'1 次/分钟 · 20 秒',stopPolicy:{http_error_rate:.1,grace_seconds:10}}
  const wrapper=mount(LoadRunWizard,{props:{scenario,environments,agents,preset}})
  await flushPromises()
  expect(wrapper.emitted('submit')).toBeUndefined()
  expect(wrapper.get('[data-testid="load-next-review"]').text()).toContain('1 次/分钟')
  expect(wrapper.get('[data-testid="load-run-environment"]').attributes('disabled')).toBeDefined()
  await wrapper.get('[data-testid="load-agent-a1"]').setValue(true)
  await wrapper.get('[data-testid="load-run-submit"]').trigger('click')
  const payload=wrapper.emitted('submit')![0][0] as Record<string,unknown>
  expect(payload.scenario_version_id).toBe('old-v')
  expect(payload.thresholds).toEqual(preset.thresholds)
  expect(payload.monitoring).toEqual(preset.monitoring)
  expect(payload.stop_policy).toEqual(preset.stopPolicy)
  expect(payload.workload).toMatchObject({rate:2,time_unit:'1s',duration_seconds:60})
})
