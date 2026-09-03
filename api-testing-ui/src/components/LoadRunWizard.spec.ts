// @vitest-environment jsdom

import { mount } from '@vue/test-utils'
import { afterEach, describe, expect, it } from 'vitest'
import LoadRunWizard from './LoadRunWizard.vue'
import { setApiTestingAccessProfile } from '../utils/authRedirect'

const scenario = { id: 's1', project_id: 'p1', name: '搜索链路', description: '', scenario_type: 'single_interface' as const, active_version_id: 'v1', status: 'active', created_at: '', updated_at: '' }
const environments = [{ id: 'env-v1', environment_id: 'env', project_id: 'p1', name: '性能测试环境', revision: 1 }]
const agents = [{ id: 'a1', name: '专用节点', status: 'online', scheduling_tier: 'preferred' as const, node_group: '上海', labels: {}, agent_version: '1', k6_version: 'k6', hard_limits: { max_processes: 1, max_vus: 500, max_iterations_per_second: 2000, max_duration_seconds: 1800, cpu_cores: 8, memory_mb: 16384 }, soft_limits: { max_processes: 1, max_vus: 400, max_iterations_per_second: 1500, max_duration_seconds: 1200, cpu_cores: 8, memory_mb: 12000 }, current_usage: { processes: 0, vus: 0 }, health: { calibration: { state: 'valid', max_vus: 320, max_iterations_per_second: 1200, valid_until: '2099-01-01' } }, calibration_state: 'valid' as const, egress_ip: '', last_heartbeat_at: '', offline_reason: '' }]

describe('LoadRunWizard', () => {
  afterEach(() => setApiTestingAccessProfile(null))
  it('does not pretend VU multiplied by seconds is an exact iteration count', () => {
    const wrapper = mount(LoadRunWizard, { props: { scenario, environments, agents } })
    expect(wrapper.text()).toContain('固定并发会在时长内持续循环')
    expect(wrapper.text()).toContain('实际次数取决于接口响应时间')
    expect(wrapper.text()).not.toContain('预计约 1200 次完整链路')
  })

  it('explains all four load models and emits target, thresholds, allocation and priority', async () => {
    const wrapper = mount(LoadRunWizard, { props: { scenario, environments, agents } })
    expect(wrapper.text()).toContain('固定并发')
    expect(wrapper.text()).toContain('阶梯并发')
    expect(wrapper.text()).toContain('固定吞吐')
    expect(wrapper.text()).toContain('阶梯吞吐')
    await wrapper.get('[data-testid="load-model-constant-arrival-rate"]').trigger('click')
    await wrapper.get('[data-testid="load-rate"]').setValue('100')
    await wrapper.get('[data-testid="load-duration"]').setValue('60')
    await wrapper.get('[data-testid="load-p95"]').setValue('500')
    await wrapper.get('[data-testid="load-agent-a1"]').setValue(true)
    expect(wrapper.text()).toContain('预计约 6000 次完整链路')
    expect(wrapper.text()).toContain('当前可用 320 VU / 1200 次/秒')
    await wrapper.get('[data-testid="load-run-submit"]').trigger('click')
    expect(wrapper.emitted('submit')?.[0]?.[0]).toMatchObject({
      scenario_version_id: 'v1', environment_revision_id: 'env-v1',
      workload: { executor: 'constant-arrival-rate', rate: 100, time_unit: '1s', duration_seconds: 60, pre_allocated_vus: 20, max_vus: 100 },
      thresholds: { p95_ms: { operator: 'less_than_or_equal', value: 500, required: true } },
      priority: 'normal', allocation_policy: { agent_ids: ['a1'], allow_fallback: false },
    })
  })

  it.each([
    ['constant-vus', { executor: 'constant-vus', vus: 20, duration_seconds: 60 }],
    ['ramping-vus', { executor: 'ramping-vus', start_vus: 1, stages: [{ duration_seconds: 60, target: 20 }] }],
    ['ramping-arrival-rate', { executor: 'ramping-arrival-rate', start_rate: 1, time_unit: '1s', pre_allocated_vus: 20, max_vus: 100, stages: [{ duration_seconds: 60, target: 50 }] }],
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
