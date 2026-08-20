// @vitest-environment jsdom

import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'

import AiAssistant from './AiAssistant.vue'

describe('AiAssistant', () => {
  it('defaults to business and parameter cases instead of runtime header cases', () => {
    const wrapper = mount(AiAssistant, {
      props: { selectedCount: 1, job: null },
    })
    const textarea = wrapper.get('textarea').element as HTMLTextAreaElement

    expect(textarea.value).toContain('Body')
    expect(textarea.value).toContain('环境自动注入')
    expect(textarea.value).not.toContain('覆盖正常流程、鉴权')
    expect(textarea.value).not.toContain('生成正常、边界与鉴权')
  })

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

  it('emits a separate event for deterministic basic positive case generation', async () => {
    const wrapper = mount(AiAssistant, {
      props: { selectedCount: 2, job: null },
    })

    await wrapper.get('[data-testid="generate-basic-positive"]').trigger('click')

    expect(wrapper.emitted('generate-basic')).toEqual([[]])
  })
})
