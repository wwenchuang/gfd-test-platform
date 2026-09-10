// @vitest-environment jsdom
import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'
import LoadBottleneckEvidence from './LoadBottleneckEvidence.vue'

const rows = [
  {domain:'cpu',label:'CPU',status:'partial',evidence_ids:['monitoring.m1'],limitations:['缺少节流证据'],next_verification:'保持流量，补采节流并复验'},
  {domain:'database',label:'数据库',status:'missing',evidence_ids:[],limitations:['未配置数据库监控'],next_verification:'接入SQL耗时与锁等待'},
  {domain:'load_generator',label:'压力机',status:'available',evidence_ids:['agent.a1'],limitations:['采样覆盖不等于根因确认'],next_verification:'保持全局目标，只调整节点'},
]
describe('LoadBottleneckEvidence', () => {
  it('counts declared inapplicable domains separately from missing evidence', () => {
    const wrapper=mount(LoadBottleneckEvidence,{props:{rows:[...rows,{domain:'downstream',label:'下游依赖',status:'not_applicable',evidence_ids:['service.fact.demo.downstream'],limitations:['操作人声明不存在下游'],next_verification:'变更后重新核对'}]}})
    expect(wrapper.get('summary').text()).toContain('待采集 1 · 明确不适用 1')
  })
  it('summarizes neutral coverage and keeps details collapsed', () => {
    const wrapper=mount(LoadBottleneckEvidence,{props:{rows}})
    expect(wrapper.text()).toContain('证据覆盖')
    expect(wrapper.text()).toContain('可用 1')
    expect(wrapper.text()).toContain('部分 1')
    expect(wrapper.text()).toContain('待采集 1')
    expect(wrapper.get('details').attributes('open')).toBeUndefined()
    expect(wrapper.text()).toContain('不代表故障或根因已经确认')
    expect(wrapper.findAll('button')).toHaveLength(0)
  })
  it('shows each limitation, verification and references without executing actions', () => {
    const wrapper=mount(LoadBottleneckEvidence,{props:{rows}})
    expect(wrapper.text()).toContain('缺少节流证据')
    expect(wrapper.text()).toContain('接入SQL耗时与锁等待')
    expect(wrapper.text()).toContain('monitoring.m1')
    expect(wrapper.findAll('article')).toHaveLength(3)
  })
  it('handles absent evidence without inventing coverage', () => {
    const wrapper=mount(LoadBottleneckEvidence,{props:{rows:[]}})
    expect(wrapper.text()).toContain('暂无证据覆盖清单')
    expect(wrapper.find('details').exists()).toBe(false)
  })
})
