// @vitest-environment jsdom
import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'
import LoadMetricChart from './LoadMetricChart.vue'

describe('LoadMetricChart', () => {
  it('shows an SVG, an equivalent table and missing evidence windows', async () => {
    const wrapper = mount(LoadMetricChart, { props: { missingWindows: 2, series: [
      { started_at: '08:00:00', requests: 100, p95_ms: 220 },
      { started_at: '08:00:05', requests: 120, p95_ms: 350 },
    ] } })
    expect(wrapper.get('svg').attributes('aria-label')).toBe('P95响应时间折线图')
    expect(wrapper.text()).toContain('缺失 2 个窗口')
    await wrapper.get('summary').trigger('click')
    expect(wrapper.text()).toContain('08:00:05')
    expect(wrapper.text()).toContain('350')
  })
})

 it('explains units and does not draw missing latency as zero or bridge missing windows', () => {
   const wrapper = mount(LoadMetricChart, { props: { series: [
     { started_at: '2026-09-07T08:00:00Z', requests: 10, p95_ms: 200 },
     { started_at: '2026-09-07T08:00:05Z', requests: 0, p95_ms: null },
     { started_at: '2026-09-07T08:00:15Z', requests: 10, p95_ms: 300 },
   ] } })
   expect(wrapper.text()).toContain('95%')
   expect(wrapper.text()).toContain('毫秒')
   expect(wrapper.text()).toContain('暂无样本')
   expect(wrapper.findAll('circle')).toHaveLength(2)
   expect(wrapper.findAll('polyline').every(line => !(line.attributes('points') || '').includes(' '))).toBe(true)
 })

it('shows a single real zero-latency sample and an explicitly configured reference', async () => {
  const wrapper = mount(LoadMetricChart, { props: { referenceP95: 200, series: [{ started_at: '08:00:00', requests: 1, p95_ms: 0 }] } })
  expect(wrapper.findAll('circle')).toHaveLength(1)
  expect(wrapper.text()).toContain('全程 P95 要求 ≤ 200 毫秒')
  await wrapper.get('circle').trigger('focus')
  expect(wrapper.get('.chart-readout').text()).toContain('P95 0 毫秒')
})
it('does not render a zero-latency chart when there are no requests', () => {
  const wrapper = mount(LoadMetricChart, { props: { series: [{ started_at: '08:00:00', requests: 0, p95_ms: 0 }] } })
  expect(wrapper.find('svg').exists()).toBe(false)
  expect(wrapper.text()).toContain('不能据此判断响应速度')
})
