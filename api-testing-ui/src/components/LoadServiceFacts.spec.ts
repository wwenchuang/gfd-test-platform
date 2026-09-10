// @vitest-environment jsdom
import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'
import LoadServiceFacts from './LoadServiceFacts.vue'
import { serviceFactsIssue } from '../utils/loadServiceFacts'

describe('LoadServiceFacts', () => {
  it.each([
    ['bounded_work_slots', {limit:4}],
    ['bounded_connection_slots', {limit:8}],
    ['cpu_wall_time_work', {duration_ms:500,endpoints:['/demo/cpu','/demo/cpu-alt']}],
    ['shared_ttl_allocation', {bytes:1048576,ttl_seconds:30,refresh_on_request:false,endpoints:['/demo/memory']}],
  ])('edits %s parameters and removes them when marked absent', async (key, parameters) => {
    const original = {allow_private_network:true,custom_tag:'keep'}
    const wrapper=mount(LoadServiceFacts,{props:{modelValue:original,'onUpdate:modelValue':value=>wrapper.setProps({modelValue:value})}})
    const row=wrapper.get(`[data-fact="${key}"]`)
    await row.get('[data-field="state"]').setValue('present')
    await row.get('[data-field="source-kind"]').setValue('runtime_observation')
    await row.get('[data-field="reference"]').setValue('运行记录 2026-09-10')
    for(const [field,value] of Object.entries(parameters)) await row.get(`[data-field="${field}"]`).setValue(Array.isArray(value)?value.join('\n'):String(value))
    const metadata=wrapper.props('modelValue')
    expect(serviceFactsIssue(metadata)).toBe('')
    expect(metadata).toEqual({...original,load_service_facts:{version:1,components:[],behaviors:[{kind:key,state:'present',source:{kind:'runtime_observation',reference:'运行记录 2026-09-10'},...parameters}]}})
    await row.get('[data-field="state"]').setValue('absent')
    expect(wrapper.props('modelValue')).toEqual({...original,load_service_facts:{version:1,components:[],behaviors:[{kind:key,state:'absent',source:{kind:'runtime_observation',reference:'运行记录 2026-09-10'}}]}})
    expect(original).toEqual({allow_private_network:true,custom_tag:'keep'})
  })

  it('shows recorded source and unknown historical fields without readonly mutation controls', () => {
    const wrapper=mount(LoadServiceFacts,{props:{readOnly:true,modelValue:{load_service_facts:{version:1,components:[{key:'database',state:'absent',source:{kind:'source_review',reference:'app.py @ abc',recorded_by_operator:true}}],behaviors:[]}}}})
    expect(wrapper.text()).toContain('数据库：不存在')
    expect(wrapper.text()).toContain('下游依赖：未记录（未知）')
    expect(wrapper.text()).toContain('人工记录来源：代码核查记录 · app.py @ abc')
    expect(wrapper.text()).toContain('不代表平台自动验证')
    expect(wrapper.findAll('input,select,textarea,button')).toHaveLength(0)
    expect(wrapper.emitted('update:modelValue')).toBeUndefined()
  })

  it('rejects incomplete optional sources and oversized endpoint paths before saving', () => {
    const facts={version:1,components:[{key:'database',state:'unknown',source:{kind:'source_review',reference:''}}],behaviors:[]}
    expect(serviceFactsIssue({load_service_facts:facts})).toContain('请填写来源与引用')
    expect(serviceFactsIssue({load_service_facts:{version:1,components:[],behaviors:[{kind:'cpu_wall_time_work',state:'present',source:{kind:'operator_declaration',reference:'checked'},duration_ms:100,endpoints:['/'+ 'x'.repeat(500)]}]}})).toContain('接口路径')
  })
})
