// @vitest-environment jsdom
import { mount } from '@vue/test-utils'
import { afterEach, describe, expect, it, vi } from 'vitest'
import LoadRunReplayPanel from './LoadRunReplayPanel.vue'

function report(overrides: Record<string, unknown> = {}) {
  return {
    run_id: 'run-replay',
    state: 'finished',
    load_goal: {
      model: 'ramping-arrival-rate',
      stages: [
        { start_seconds: 0, duration_seconds: 15, start_rate: 1, target_rate: 2 },
        { start_seconds: 15, duration_seconds: 15, start_rate: 2, target_rate: 4 },
        { start_seconds: 30, duration_seconds: 15, start_rate: 4, target_rate: 2 },
      ],
    },
    thresholds: [{ key: 'p95_ms', expected: 1200 }],
    series: [
      { started_at: '2026-09-10T04:20:50Z', requests: 15, iterations: 5, http_failures: 0, business_failures: 0, p95_ms: 900 },
      { started_at: '2026-09-10T04:20:55Z', requests: 18, iterations: 6, http_failures: 0, business_failures: 0, p95_ms: 1300 },
      { started_at: '2026-09-10T04:21:00Z', requests: 21, iterations: 7, http_failures: 0, business_failures: 0, p95_ms: 1400 },
      { started_at: '2026-09-10T04:21:05Z', requests: 24, iterations: 8, http_failures: 0, business_failures: 0, p95_ms: 1500 },
    ],
    evidence: {
      workload_snapshot: {
        executor: 'ramping-arrival-rate',
        start_rate: 1,
        time_unit: '1s',
        stages: [
          { duration_seconds: 15, target: 2 },
          { duration_seconds: 15, target: 4 },
          { duration_seconds: 15, target: 2 },
        ],
      },
    },
    monitoring: { services: [] },
    ...overrides,
  } as any
}

function run(overrides: Record<string, unknown> = {}) {
  return {
    id: 'run-replay',
    state: 'finished',
    load_model: 'ramping-arrival-rate',
    started_at: '2026-09-10T04:20:50Z',
    finished_at: '2026-09-10T04:21:10Z',
    configuration: {},
    ...overrides,
  } as any
}

afterEach(() => {
  vi.useRealTimers()
})

describe('LoadRunReplayPanel', () => {
  it('uses the frozen workload when the run configuration has no workload', () => {
    const wrapper = mount(LoadRunReplayPanel, { props: { run: run(), report: report() } })
    expect(wrapper.text()).toContain('计划 1 次/秒')
    expect(wrapper.text()).toContain('阶段 3 开始')
  })

  it('reads monitored resource points from every stored metric series', () => {
    const monitoring = {
      services: [{
        revision_id: 'demo-monitor-v5',
        name: '演示服务容器',
        metrics: [
          { key: 'cpu_cores', label: 'CPU 使用量', series: [{ labels: { id: '/demo' }, points: [{ timestamp: 1789014050, value: 0.11 }, { timestamp: 1789014055, value: 0.12 }] }] },
          { key: 'memory_working_set_bytes', label: '工作集内存', series: [{ labels: { id: '/demo' }, points: [{ timestamp: 1789014050, value: 95776932 }, { timestamp: 1789014055, value: 95800000 }] }] },
        ],
      }],
    }
    const wrapper = mount(LoadRunReplayPanel, { props: { run: run(), report: report({ monitoring }) } })
    expect(wrapper.text()).toContain('演示服务容器（id=/demo） · CPU 使用量 0.11 cores')
    expect(wrapper.text()).toContain('演示服务容器（id=/demo） · 工作集内存')
    expect(wrapper.text()).not.toContain('CPU 证据缺失')
    expect(wrapper.text()).not.toContain('内存证据缺失')
    expect(wrapper.text()).toContain('缺少 CPU 百分比或配额，不能判断高占用')
  })

  it('toggles playback with the same play and pause control', async () => {
    vi.useFakeTimers()
    const wrapper = mount(LoadRunReplayPanel, { props: { run: run(), report: report() } })
    const control = wrapper.get('[data-testid="replay-toggle"]')
    expect(control.text()).toBe('播放')
    await control.trigger('click')
    expect(control.text()).toBe('暂停')
    await control.trigger('click')
    expect(control.text()).toBe('播放')
  })

  it('does not count a missing P95 window as recovery evidence', () => {
    const series = [
      { started_at: '2026-09-10T04:20:50Z', requests: 10, iterations: 10, p95_ms: 1300 },
      { started_at: '2026-09-10T04:20:55Z', requests: 10, iterations: 10, p95_ms: 1400 },
      { started_at: '2026-09-10T04:21:00Z', requests: 10, iterations: 10, p95_ms: 1500 },
      { started_at: '2026-09-10T04:21:05Z', requests: 10, iterations: 10, p95_ms: null },
      { started_at: '2026-09-10T04:21:10Z', requests: 10, iterations: 10, p95_ms: 900 },
      { started_at: '2026-09-10T04:21:15Z', requests: 10, iterations: 10, p95_ms: 800 },
    ]
    const wrapper = mount(LoadRunReplayPanel, { props: { run: run({ finished_at: '2026-09-10T04:21:20Z' }), report: report({ series }) } })
    expect(wrapper.text()).toContain('恢复观察不足')
    expect(wrapper.text()).not.toContain('开始恢复观察')
  })
})
