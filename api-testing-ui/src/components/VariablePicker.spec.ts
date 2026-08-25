// @vitest-environment jsdom

import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'

import VariablePicker from './VariablePicker.vue'

const OPTIONS = [
  { name: 'modelSn', source: '前置步骤 2 · 查询模型', sourceKind: 'setup' as const, available: true },
  { name: 'printTaskSn', source: '主体响应', sourceKind: 'main' as const, available: true },
  { name: 'legacyId', source: '未找到来源', sourceKind: 'unknown' as const, available: false },
]

describe('VariablePicker', () => {
  it('filters variables by name and source and marks missing legacy values', async () => {
    const wrapper = mount(VariablePicker, { props: { modelValue: ['legacyId'], options: OPTIONS } })
    expect(wrapper.text()).toContain('未找到来源')
    await wrapper.get('[data-testid="variable-search"]').setValue('主体')
    expect(wrapper.text()).toContain('printTaskSn')
    expect(wrapper.text()).not.toContain('modelSn')
  })

  it('selects and removes required variables without comma-separated input', async () => {
    const wrapper = mount(VariablePicker, { props: { modelValue: [], options: OPTIONS } })
    await wrapper.get('[data-testid="variable-option-modelSn"]').trigger('click')
    expect(wrapper.emitted('update:modelValue')?.at(-1)?.[0]).toEqual(['modelSn'])
  })
})
