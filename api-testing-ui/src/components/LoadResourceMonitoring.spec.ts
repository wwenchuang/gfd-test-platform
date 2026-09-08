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
  it('keeps container and device identities visible and never labels unknown quota as zero', () => {
    const w = mount(LoadResourceMonitoring, { props: { monitoring: { state:'completed', services:[{name:'API Pod',scope:'pod',state:'completed',metrics:[{key:'cpu_cores',unit:'cores',denominator:'container_cpu_quota_cores',series:[{labels:{instance:'node',container:'api',id:'/pod/api'},points:[{timestamp:100,value:0.5,denominator_value:null,used_value:0.5,utilization_percent:null}]}]}]}]}}})
    expect(w.text()).toContain('指定 Pod 内各容器')
    expect(w.text()).toContain('container=api')
    expect(w.text()).toContain('未知 / 无配额')
    expect(w.text()).not.toContain('采集范围：整台主机')
  })

  it('labels zero-operation latency separately from missing exporter samples', () => {
    const w = mount(LoadResourceMonitoring, { props: {monitoring:{state:'partial',services:[{name:'磁盘',scope:'host',state:'missing',metrics:[{key:'disk_read_latency_ms',unit:'ms',series:[{labels:{instance:'node',device:'sda'},points:[{timestamp:100,value:null,missing_reason:'no_operations'}]}]}]}]}}})
    expect(w.text()).toContain('无 I/O，延迟不可定义')
    expect(w.text()).toContain('device=sda')
  })

})
