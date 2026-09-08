// @vitest-environment jsdom
import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'
import LoadResourceMonitoring from './LoadResourceMonitoring.vue'

describe('resource monitoring evidence', () => {
  it('does not display missing monitoring as zero usage', () => {
    const w = mount(LoadResourceMonitoring, { props: { monitoring: { state: 'not_selected', services: [] } } })
    expect(w.text()).toContain('未选择资源监控')
    expect(w.text()).not.toContain('0%')
  })
  it('shows source, host scope, units and gaps without joining missing samples', () => {
    const w = mount(LoadResourceMonitoring, { props: { monitoring: { state: 'completed', services: [{ name: '模型服务所在主机', scope: 'host', state: 'completed', source_url: 'https://metrics.example', step_seconds: 15, metrics: [{ key: 'cpu_percent', label: 'CPU 使用率', unit: '%', denominator: '整机全部逻辑核', series: [{ labels: { instance: 'node:9100' }, points: [{ timestamp: 100, value: 50 }, { timestamp: 115, value: null }, { timestamp: 130, value: 60 }] }] }] }] } } })
    expect(w.text()).toContain('整机全部逻辑核')
    expect(w.text()).toContain('不是单个业务服务的独占资源')
    expect(w.text()).toContain('metrics.example')
    expect(w.text()).toContain('采样缺失')
    expect(w.findAll('polyline')).toHaveLength(2)
  })
})
