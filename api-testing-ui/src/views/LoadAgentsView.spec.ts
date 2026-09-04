// @vitest-environment jsdom

import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import type { LoadAgent } from '../api/contracts'
import { useLoadTestingStore } from '../stores/loadTesting'
import { setApiTestingAccessProfile } from '../utils/authRedirect'
import { installImeCompositionGuard } from '../utils/imeCompositionGuard'
import LoadAgentsView from './LoadAgentsView.vue'

const LIMITS = {
  max_processes: 2, max_vus: 500, max_iterations_per_second: 2000,
  max_duration_seconds: 1800, cpu_cores: 8, memory_mb: 16384,
}

function agent(overrides: Partial<LoadAgent> = {}): LoadAgent {
  return {
    id: 'agent-1', name: '腾讯云专用节点', status: 'online', scheduling_tier: 'preferred',
    node_group: '腾讯云', labels: {}, agent_version: '1.0.0', k6_version: 'k6 v0.52.0',
    hard_limits: LIMITS, soft_limits: { ...LIMITS, max_vus: 400, max_iterations_per_second: 1500 },
    current_usage: { processes: 0, vus: 0, iterations_per_second: 0 },
    health: { schedulable: true, calibration: {
      state: 'valid', calibrated_at: '2026-09-03T12:00:00+08:00', valid_until: '2026-09-10T12:00:00+08:00',
      max_vus: 320, max_iterations_per_second: 1200,
    } },
    calibration_state: 'valid', egress_ip: '203.0.113.8',
    last_heartbeat_at: '2026-09-03T12:03:00+08:00', offline_reason: '',
    ...overrides,
  }
}

describe('LoadAgentsView', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    vi.restoreAllMocks()
    setApiTestingAccessProfile({ status: 'active', permissions: ['api.view', 'api.loadtest.view', 'api.loadtest.manage_agents'] })
  })

  it('explains tier, hard/soft/calibrated capacity and executes every node action', async () => {
    const store = useLoadTestingStore()
    vi.spyOn(store, 'loadAgents').mockImplementation(async () => { store.agents = [agent()]; return store.agents })
    const update = vi.spyOn(store, 'updateAgent').mockResolvedValue(agent({ scheduling_tier: 'fallback' }))
    const calibrate = vi.spyOn(store, 'calibrateAgent').mockResolvedValue(agent({ calibration_state: 'calibrating' }))
    const wrapper = mount(LoadAgentsView)
    await flushPromises()

    expect(wrapper.text()).toContain('首选节点')
    expect(wrapper.text()).toContain('优先承接压测')
    expect(wrapper.text()).toContain('本机硬上限')
    expect(wrapper.text()).toContain('平台容量策略')
    expect(wrapper.text()).toContain('校准达到值')
    expect(wrapper.text()).toContain('进程 2')
    expect(wrapper.text()).toContain('最长 1800 秒')
    expect(wrapper.text()).toContain('CPU 8 核')
    expect(wrapper.text()).toContain('内存 16384 MB')
    expect(wrapper.text()).toContain('320 VU')
    expect(wrapper.text()).toContain('1200 次/秒')
    expect(wrapper.text()).toContain('有效至')
    expect(wrapper.get('[data-testid="load-agent-summary"]').text()).toContain('节点总数1')
    expect(wrapper.get('[data-testid="load-agent-summary"]').text()).toContain('心跳正常1')
    expect(wrapper.get('[data-testid="load-agent-summary"]').text()).toContain('可调度1')
    expect(wrapper.get('[data-testid="load-agent-summary"]').text()).toContain('校准达到320 VU')
    expect(wrapper.get('[data-testid="load-agent-summary"]').text()).toContain('当前可分配320 VU')
    expect(wrapper.text()).toContain('三项取最小值')
    expect(wrapper.text()).toContain('受校准达到值限制')
    const heartbeat = wrapper.get('[data-testid="load-agent-heartbeat-agent-1"]')
    expect(heartbeat.text()).toContain('心跳正常')
    expect(heartbeat.find('.load-status-dot').classes()).toContain('pulse')

    await wrapper.get('[data-testid="agent-tier-agent-1"]').setValue('fallback')
    await flushPromises()
    expect(update).toHaveBeenCalledWith('agent-1', { scheduling_tier: 'fallback' })
    await wrapper.get('[data-testid="agent-calibrate-agent-1"]').trigger('click')
    await flushPromises()
    expect(calibrate).toHaveBeenCalledWith('agent-1')
    await wrapper.get('[data-testid="load-agents-refresh"]').trigger('click')
    expect(store.loadAgents).toHaveBeenCalledTimes(2)
  })

  it('configures soft limits from the page without requiring server access', async () => {
    const store = useLoadTestingStore()
    vi.spyOn(store, 'loadAgents').mockImplementation(async () => { store.agents = [agent()]; return store.agents })
    const update = vi.spyOn(store, 'updateAgent').mockResolvedValue(agent())
    const wrapper = mount(LoadAgentsView)
    await flushPromises()

    await wrapper.get('[data-testid="agent-capacity-open-agent-1"]').trigger('click')
    expect(wrapper.text()).toContain('配置平台容量策略')
    expect(wrapper.text()).toContain('无需登录服务器')
    await wrapper.get('[data-testid="capacity-preset-dedicated"]').trigger('click')
    await wrapper.get('[data-testid="capacity-soft-vus"]').setValue('350')
    await wrapper.get('[data-testid="capacity-soft-save"]').trigger('click')
    await flushPromises()
    expect(update).toHaveBeenCalledWith('agent-1', { soft_limits: expect.objectContaining({ max_vus: 350 }) })
  })

  it('supports Chinese IME search and shows all calibration states in Chinese', async () => {
    const store = useLoadTestingStore()
    const states: Array<LoadAgent['calibration_state']> = ['uncalibrated', 'calibrating', 'valid', 'expired', 'invalidated', 'failed']
    vi.spyOn(store, 'loadAgents').mockImplementation(async () => {
      store.agents = states.map((state, index) => agent({ id: `agent-${index}`, name: index ? `节点${index}` : '上海压测节点', calibration_state: state, health: { calibration: { state } } }))
      return store.agents
    })
    const uninstall = installImeCompositionGuard(document)
    const wrapper = mount(LoadAgentsView, { attachTo: document.body })
    await flushPromises()
    expect(wrapper.text()).toContain('未校准')
    expect(wrapper.text()).toContain('校准中')
    expect(wrapper.text()).toContain('校准有效')
    expect(wrapper.text()).toContain('校准过期')
    expect(wrapper.text()).toContain('配置变化后失效')
    expect(wrapper.text()).toContain('校准失败')

    const input = wrapper.get('[data-testid="load-agent-search"]').element as HTMLInputElement
    input.dispatchEvent(new CompositionEvent('compositionstart', { bubbles: true }))
    input.value = '上'
    input.dispatchEvent(new InputEvent('input', { bubbles: true, isComposing: true }))
    expect(wrapper.findAll('[data-testid^="load-agent-card-"]')).toHaveLength(6)
    input.value = '上海'
    input.dispatchEvent(new CompositionEvent('compositionend', { bubbles: true, data: '上海' }))
    await new Promise(resolve => setTimeout(resolve, 0))
    await flushPromises()
    expect(wrapper.findAll('[data-testid^="load-agent-card-"]')).toHaveLength(1)
    uninstall()
    wrapper.unmount()
  })

  it('shows the Agent calibration failure reason instead of only generic advice', async () => {
    const store = useLoadTestingStore()
    vi.spyOn(store, 'loadAgents').mockImplementation(async () => {
      store.agents = [agent({
        calibration_state: 'failed',
        health: { schedulable: false, calibration: {
          state: 'failed', message: '校准结果缺少迭代率或虚拟用户数',
        } },
      })]
      return store.agents
    })
    const wrapper = mount(LoadAgentsView)
    await flushPromises()

    expect(wrapper.text()).toContain('失败原因：校准结果缺少迭代率或虚拟用户数')
  })

  it('automatically replaces calibrating with the returned result without manual refresh', async () => {
    vi.useFakeTimers()
    const store = useLoadTestingStore()
    let calls = 0
    const load = vi.spyOn(store, 'loadAgents').mockImplementation(async () => {
      calls += 1
      store.agents = [agent(calls === 1
        ? { calibration_state: 'calibrating', health: { schedulable: false, calibration: { state: 'calibrating' } } }
        : { calibration_state: 'valid' })]
      return store.agents
    })
    const wrapper = mount(LoadAgentsView)
    await flushPromises()

    expect(wrapper.text()).toContain('校准中')
    await vi.advanceTimersByTimeAsync(3000)
    await flushPromises()
    expect(load).toHaveBeenLastCalledWith(true)
    expect(wrapper.text()).toContain('校准有效')
    wrapper.unmount()
    vi.useRealTimers()
  })

  it('creates a one-time enrollment, copies its command and warns about HTTP transport', async () => {
    Object.defineProperty(navigator, 'clipboard', { configurable: true, value: { writeText: vi.fn().mockResolvedValue(undefined) } })
    const store = useLoadTestingStore()
    vi.spyOn(store, 'loadAgents').mockResolvedValue([])
    vi.spyOn(store, 'createEnrollment').mockResolvedValue({
      id: 'enrollment-1', token: 'one-time-token', expires_at: '2026-09-03T12:15:00+08:00',
      credential_notice: '注册令牌仅显示一次，请立即保存',
    })
    const wrapper = mount(LoadAgentsView)
    await flushPromises()
    await wrapper.get('[data-testid="load-agent-enroll-open"]').trigger('click')
    expect(wrapper.text()).toContain('服务器不需要安装 Git')
    expect(wrapper.text()).toContain('最小 Agent 包')
    await wrapper.get('[data-testid="enrollment-name"]').setValue('第二台专用节点')
    await wrapper.get('[data-testid="enrollment-group"]').setValue('阿里云')
    await wrapper.get('[data-testid="enrollment-tier"]').setValue('preferred')
    await wrapper.get('[data-testid="enrollment-submit"]').trigger('click')
    await flushPromises()

    expect(wrapper.text()).toContain('令牌只显示这一次')
    expect(wrapper.text()).toContain('ENROLL_TOKEN=')
    expect(wrapper.text()).toContain('bash deploy/load-agent/install.sh')
    expect(wrapper.text()).toContain('Agent 包根目录')
    expect(wrapper.text()).not.toContain('load-agent-compose.yml')
    expect(wrapper.text()).toContain('当前平台仍是 HTTP')
    await wrapper.get('[data-testid="enrollment-copy"]').trigger('click')
    await flushPromises()
    expect(navigator.clipboard.writeText).toHaveBeenCalledWith(expect.stringContaining("ENROLL_TOKEN='one-time-token'"))
    expect(navigator.clipboard.writeText).toHaveBeenCalledWith(expect.stringContaining('bash deploy/load-agent/install.sh'))
    expect(wrapper.text()).toContain('启动命令已复制')
    await wrapper.get('[data-testid="enrollment-close"]').trigger('click')
    expect(wrapper.find('[data-testid="enrollment-result"]').exists()).toBe(false)
  })

  it('shows loading, empty, error and explicit disabled reasons', async () => {
    const store = useLoadTestingStore()
    vi.spyOn(store, 'loadAgents').mockImplementation(async () => [])
    store.loadingAgents = true
    let wrapper = mount(LoadAgentsView)
    expect(wrapper.text()).toContain('正在读取压测节点')
    wrapper.unmount()

    store.loadingAgents = false
    store.agents = []
    wrapper = mount(LoadAgentsView)
    await flushPromises()
    expect(wrapper.text()).toContain('还没有压测节点')
    wrapper.unmount()

    vi.mocked(store.loadAgents).mockImplementation(async () => { store.agentError = '节点服务不可用'; return [] })
    wrapper = mount(LoadAgentsView)
    await flushPromises()
    expect(wrapper.get('[role="alert"]').text()).toContain('节点服务不可用')
    wrapper.unmount()

    setApiTestingAccessProfile({ status: 'active', permissions: ['api.view', 'api.loadtest.view'] })
    vi.mocked(store.loadAgents).mockImplementation(async () => { store.agentError = ''; store.agents = [agent({ status: 'offline' })]; return store.agents })
    wrapper = mount(LoadAgentsView)
    await flushPromises()
    const button = wrapper.get('[data-testid="agent-calibrate-agent-1"]')
    expect(button.attributes('disabled')).toBeDefined()
    expect(wrapper.text()).toContain('需要节点管理权限')
    expect(wrapper.get('[data-testid="load-agent-heartbeat-agent-1"]').text()).toContain('心跳中断')
  })
})
