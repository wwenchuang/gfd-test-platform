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
