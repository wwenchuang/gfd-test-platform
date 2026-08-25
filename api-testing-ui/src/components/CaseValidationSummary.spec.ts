// @vitest-environment jsdom

import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'

import CaseValidationSummary from './CaseValidationSummary.vue'

describe('CaseValidationSummary', () => {
  it('summarizes workflow counts and navigates to an errored field', async () => {
    const wrapper = mount(CaseValidationSummary, {
      props: {
        setupCount: 2, assertionCount: 3, cleanupCount: 1,
        errors: { 'processing.cleanup_steps[0].request.path': '请求路径不能为空' },
        warnings: {},
      },
    })
    expect(wrapper.text()).toContain('前置 2')
    expect(wrapper.text()).toContain('错误 1')
    expect(wrapper.text()).toContain('清理 1')
    await wrapper.get('[data-testid="validation-issue-0"]').trigger('click')
    expect(wrapper.emitted('navigate')?.[0]).toEqual(['processing.cleanup_steps[0].request.path'])
  })
})
