// @vitest-environment jsdom

import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'

import CaseGroupPicker from './CaseGroupPicker.vue'

describe('CaseGroupPicker', () => {
  it('searches existing groups and emits the chosen group', async () => {
    const wrapper = mount(CaseGroupPicker, {
      props: { groups: ['家用业务 / 收藏', '家用业务 / 设备', 'API Test / 一次性'], currentGroup: '家用业务 / 收藏' },
    })

    await wrapper.get('[data-testid="case-group-picker-search"]').setValue('设备')

    expect(wrapper.text()).toContain('1 个匹配分组')
    expect(wrapper.text()).toContain('家用业务 / 设备')
    expect(wrapper.text()).not.toContain('API Test / 一次性')

    await wrapper.get('[data-testid="case-group-picker-option-家用业务 / 设备"]').trigger('click')
    expect(wrapper.emitted('select')?.[0]).toEqual(['家用业务 / 设备'])
  })

  it('offers a clear create-and-move command for a new group', async () => {
    const wrapper = mount(CaseGroupPicker, { props: { groups: ['家用业务 / 收藏'], currentGroup: '' } })

    await wrapper.get('[data-testid="case-group-picker-search"]').setValue('发版回归')
    expect(wrapper.get('[data-testid="case-group-picker-create"]').text()).toContain('创建并移动到“发版回归”')

    await wrapper.get('[data-testid="case-group-picker-create"]').trigger('click')
    expect(wrapper.emitted('select')?.[0]).toEqual(['发版回归'])
  })

  it('closes with Escape', async () => {
    const wrapper = mount(CaseGroupPicker, { props: { groups: [], currentGroup: '' } })
    await wrapper.get('[data-testid="case-group-picker"]').trigger('keydown', { key: 'Escape' })
    expect(wrapper.emitted('close')).toHaveLength(1)
  })
})
