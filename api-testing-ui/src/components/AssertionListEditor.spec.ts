// @vitest-environment jsdom

import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'

import AssertionListEditor from './AssertionListEditor.vue'

function latestAssertions(wrapper: ReturnType<typeof mount>): Array<Record<string, unknown>> {
  return wrapper.emitted('update:modelValue')?.at(-1)?.[0] as Array<Record<string, unknown>>
}

describe('AssertionListEditor', () => {
  it('keeps HTTP status expectations numeric', async () => {
    const wrapper = mount(AssertionListEditor, {
      props: { modelValue: [{ type: 'status_code', operator: 'equals', expected: 200, enabled: true }] },
    })
    await wrapper.get('[data-testid="assertion-expected-0"]').setValue('201')
    expect(latestAssertions(wrapper)[0].expected).toBe(201)
  })

  it('adds a default assertion with a stable test id prefix', async () => {
    const wrapper = mount(AssertionListEditor, {
      props: { modelValue: [], testIdPrefix: 'setup-0' },
    })
    await wrapper.get('[data-testid="setup-0-add-assertion"]').trigger('click')
    expect(latestAssertions(wrapper)[0]).toMatchObject({ type: 'status_code', expected: 200 })
  })

  it('uses Chinese operators and restricts business code assertions to exact matches', async () => {
    const wrapper = mount(AssertionListEditor, {
      props: {
        modelValue: [{ type: 'json_path', path: '$.data', operator: 'not_equals', expected: 0, enabled: true }],
      },
    })

    await wrapper.get('[data-testid="assertion-path-0"]').setValue('$.code')
    await wrapper.setProps({ modelValue: latestAssertions(wrapper) })

    expect(latestAssertions(wrapper)[0].operator).toBe('equals')
    const operator = wrapper.get('[data-testid="assertion-operator-0"]')
    expect(operator.text()).toContain('等于')
    expect(operator.text()).toContain('属于集合')
    expect(operator.text()).not.toContain('not_equals')
    expect(wrapper.get('[data-testid="business-code-assertion-hint-0"]').text()).toContain('精确断言')
  })
})
