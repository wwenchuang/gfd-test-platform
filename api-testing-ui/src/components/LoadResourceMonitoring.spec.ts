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

it('shows Pod readiness latest state and rolling restart estimates, not uptime or total restarts', () => {
  const w = mount(LoadResourceMonitoring, {props:{monitoring:{state:'partial',services:[{name:'API 状态',scope:'pod_state',state:'missing',source_url:'https://metrics.example',metrics:[
    {key:'pod_ready',label:'Pod 就绪状态',unit:'state',series:[{labels:{instance:'ksm:8080',namespace:'qa',pod:'api-1',uid:'u1'},points:[{timestamp:100,value:1},{timestamp:115,value:0}]}]},
    {key:'pod_restarts_increase_2m',label:'容器重启增量（前2分钟）',unit:'次',series:[{labels:{container:'api',uid:'u1'},points:[{timestamp:100,value:2.5},{timestamp:115,value:null}]}]},
  ]}]}}})
  expect(w.text()).toContain('指定 Pod 状态')
  expect(w.text()).toContain('最后观测状态')
  expect(w.text()).toContain('未确认就绪')
  expect(w.text()).toContain('滚动窗口峰值')
  expect(w.text()).toContain('2.5 次')
  expect(w.text()).toContain('采样缺失')
  expect(w.text()).not.toContain('保留真实容器标识')
})

it('does not reuse a previous ready state when the final sample is missing', () => {
  const w = mount(LoadResourceMonitoring, {props:{monitoring:{state:'partial',services:[{name:'Pod',scope:'pod_state',state:'missing',metrics:[{key:'pod_ready',unit:'state',series:[{labels:{pod:'api'},points:[{timestamp:100,value:1},{timestamp:115,value:null}]}]}]}]}}})
  const latest = w.findAll('p').find(p => p.text().includes('最后观测状态'))!
  expect(latest.text()).toContain('最后观测状态 —')
  expect(latest.text()).not.toContain('已就绪')
  expect(w.text()).toContain('地址未上报')
})

it('labels an omitted tail as a historical observation rather than current health', () => {
  const w = mount(LoadResourceMonitoring, {props:{monitoring:{state:'partial',services:[{name:'Pod',scope:'pod_state',state:'missing',metrics:[{key:'pod_ready',unit:'state',series:[{labels:{pod:'api'},points:[{timestamp:100,value:1}]}]}]}]}}})
  expect(w.text()).toContain('最后观测状态 已就绪')
  expect(w.text()).toContain('观测于')
  expect(w.text()).toContain('仅代表该采样时刻')
  expect(w.text()).not.toContain('最新状态')
})
