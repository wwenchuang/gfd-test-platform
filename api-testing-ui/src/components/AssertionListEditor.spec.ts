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
})
