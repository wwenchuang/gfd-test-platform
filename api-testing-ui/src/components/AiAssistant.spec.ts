// @vitest-environment jsdom

import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'

import AiAssistant from './AiAssistant.vue'

describe('AiAssistant', () => {
  it('shows the actionable validation reason for a failed batch', () => {
    const wrapper = mount(AiAssistant, {
      props: {
        selectedCount: 7,
        job: {
          id: 'job-1',
          state: 'failed_validation',
          endpoint_ids: ['endpoint-1'],
          requested_model: '',
          actual_model: 'qwen-plus',
          fallback_used: false,
          summary: {},
          batches: [{
            id: 'batch-1', sequence: 1, state: 'failed_validation', endpoint_ids: ['endpoint-1'],
            requested_model: '', actual_model: 'qwen-plus', fallback_used: false, fallback_reason: '',
            generated_draft_ids: [],
            validation_errors: [{ message: 'AI Gateway content is not strict JSON' }],
          }],
        },
      },
    })

    expect(wrapper.text()).toContain('AI 返回内容格式不正确，请重新生成')
  })
})
