// @vitest-environment jsdom

import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'

import ExtractionListEditor from './ExtractionListEditor.vue'

function latestExtractions(wrapper: ReturnType<typeof mount>): Array<Record<string, unknown>> {
  return wrapper.emitted('update:modelValue')?.at(-1)?.[0] as Array<Record<string, unknown>>
}

describe('ExtractionListEditor', () => {
  it('adds a required JSONPath extraction', async () => {
    const wrapper = mount(ExtractionListEditor, { props: { modelValue: [], testIdPrefix: 'setup-0' } })
    await wrapper.get('[data-testid="setup-0-add-extraction"]').trigger('click')
    expect(latestExtractions(wrapper)[0]).toMatchObject({
      type: 'json_path', path: '$.data', required: true,
    })
  })
})
