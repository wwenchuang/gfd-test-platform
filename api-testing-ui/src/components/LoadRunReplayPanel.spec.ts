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
    finished_at: '2026-09-10T04:21:35Z',
    configuration: {},
    ...overrides,
  } as any
}

afterEach(() => {
  vi.useRealTimers()
})

describe('LoadRunReplayPanel', () => {
  it('links a chart click to every chart and keeps unlike resource units separate', async () => {
    const wrapper = mount(LoadRunReplayPanel, {props:{run:run(),report:report({monitoring:{services:[{name:'演示服务',metrics:[
      {key:'cpu_cores',series:[{points:[{timestamp:Date.parse('2026-09-10T04:20:50Z')/1000,value:0.1}]}]},
      {key:'cpu_percent',series:[{points:[{timestamp:Date.parse('2026-09-10T04:20:50Z')/1000,value:20}]}]},
    ]}]}})}})
    const charts = wrapper.findAllComponents({name:'LoadReplayChart'})
    expect(charts.filter(c=>c.props('title')==='处理器使用情况').map(c=>c.props('unit'))).toEqual(['核','%'])
    await charts[0].findAll('[data-time]')[1].trigger('click')
    for (const chart of charts) expect(chart.get('[role="status"]').text()).toContain('00:05')
  })

  it('uses the frozen workload when the run configuration has no workload', () => {
    const wrapper = mount(LoadRunReplayPanel, { props: { run: run(), report: report({ evidence: {} }) } })
    expect(wrapper.text()).toContain('计划 1 次/秒')
    expect(wrapper.text()).toContain('阶段 3 开始')
  })

  it('aligns the plan with a sampling bucket that begins just before the run', () => {
    const wrapper = mount(LoadRunReplayPanel, {
      props: {
        run: run({ started_at: '2026-09-10T04:20:54Z' }),
        report: report({ evidence: {} }),
      },
    })
    expect(wrapper.text()).toContain('计划 1 次/秒')
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
    expect(wrapper.text()).toContain('演示服务容器 · 处理器使用核数：0.11 核')
    expect(wrapper.text()).toContain('演示服务容器 · 内存用量')
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

  it('keeps five-second throughput buckets and breaks anomalies across missing buckets', () => {
    const series = [0, 10, 20].map(seconds => ({
      started_at: new Date(Date.parse('2026-09-10T04:20:50Z') + seconds * 1000).toISOString(),
      requests: 30, iterations: 10, p95_ms: 1400,
    }))
    const wrapper = mount(LoadRunReplayPanel, { props: { run: run(), report: report({ series }) } })
    expect(wrapper.text()).toContain('实际 2 次/秒')
    expect(wrapper.text()).not.toContain('首次持续异常')
  })

  it('does not confirm recovery across absent sampling buckets', () => {
    const series = [0, 5, 10, 15, 25, 35].map((seconds, index) => ({
      started_at: new Date(Date.parse('2026-09-10T04:20:50Z') + seconds * 1000).toISOString(),
      requests: 10, iterations: 10, p95_ms: index < 3 ? 1400 : 900,
    }))
    const wrapper = mount(LoadRunReplayPanel, { props: { run: run(), report: report({ series }) } })
    expect(wrapper.text()).toContain('恢复观察不足')
    expect(wrapper.text()).not.toContain('开始恢复观察')
  })

  it('does not show planned stages after a stopped run as completed events', () => {
    const wrapper = mount(LoadRunReplayPanel, { props: { run: run({ state: 'cancelled', finished_at: '2026-09-10T04:21:00Z' }), report: report() } })
    expect(wrapper.text()).not.toContain('阶段 2 开始')
    expect(wrapper.text()).not.toContain('阶段 3 开始')
  })

  it('does not compare observed throughput with planned virtual users', () => {
    const wrapper = mount(LoadRunReplayPanel, { props: { run: run({ load_model: 'constant-vus', configuration: { workload: { vus: 2 } } }), report: report() } })
    expect(wrapper.text()).toContain('计划 2 VU')
    expect(wrapper.text()).toContain('实际并发未接入回放')
    expect(wrapper.text()).not.toContain('实际完整链路吞吐：')
  })

  it('interpolates the planned pressure inside a descending ramp', () => {
    const wrapper = mount(LoadRunReplayPanel, { props: { run: run(), report: report({ series: [
      { started_at: '2026-09-10T04:21:25Z', requests: 15, iterations: 5, p95_ms: 900 },
    ] }) } })
    expect(wrapper.text()).toContain('计划 3.333 次/秒')
  })

  it('keeps zero completed chains separate from HTTP throughput', () => {
    const wrapper = mount(LoadRunReplayPanel, { props: { run: run(), report: report({ series: [
      { started_at: '2026-09-10T04:20:50Z', requests: 15, iterations: 0, p95_ms: 900 },
    ] }) } })
    expect(wrapper.text()).toContain('实际 0 次/秒')
    expect(wrapper.text()).toContain('业务失败率 —')
  })

  it('uses business assertion count instead of completed chains for business failure rate', () => {
    const wrapper = mount(LoadRunReplayPanel, { props: { run: run(), report: report({ series: [
      { started_at: '2026-09-10T04:20:50Z', requests: 15, iterations: 5, business_assertions: 30, business_failures: 3, p95_ms: 900 },
    ] }) } })
    expect(wrapper.text()).toContain('业务 10%')
    expect(wrapper.text()).toContain('业务失败率 10%')
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
