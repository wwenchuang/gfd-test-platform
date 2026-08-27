// @vitest-environment jsdom

import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'

import type { AiJob, CaseVersion } from '../api/contracts'
import AiAssistant from './AiAssistant.vue'

const job: AiJob = {
  id: 'job-1', state: 'completed', endpoint_ids: ['endpoint-1'], requested_model: 'qwen-plus', actual_model: 'qwen-plus',
  fallback_used: false, summary: {},
  batches: [{
    id: 'batch-1', sequence: 1, state: 'completed', endpoint_ids: ['endpoint-1'], requested_model: 'qwen-plus', actual_model: 'qwen-plus',
    fallback_used: false, fallback_reason: '', generated_draft_ids: ['version-1', 'version-2'], validation_errors: [],
  }],
}

const generatedCases = [
  { id: 'version-1', endpoint_id: 'endpoint-1', name: '正常分页', request: { method: 'GET', path: '/list' } },
  { id: 'version-2', endpoint_id: 'endpoint-1', name: '页码边界', request: { method: 'GET', path: '/list' } },
] as CaseVersion[]

describe('AiAssistant', () => {
  it('defaults to business and parameter cases instead of runtime header cases', () => {
    const wrapper = mount(AiAssistant, { props: { selectedCount: 1, job: null } })
    const textarea = wrapper.get('textarea').element as HTMLTextAreaElement

    expect(textarea.value).toContain('请求体')
    expect(textarea.value).toContain('环境自动注入')
    expect(textarea.value).not.toContain('覆盖正常流程、鉴权')
    expect(textarea.value).not.toContain('生成正常、边界与鉴权')
  })

  it('shows the actionable validation reason for a failed batch', () => {
    const wrapper = mount(AiAssistant, {
      props: {
        selectedCount: 7,
        job: {
          ...job,
          state: 'failed_validation',
          batches: [{ ...job.batches[0], state: 'failed_validation', generated_draft_ids: [], validation_errors: [{ message: 'AI Gateway content is not strict JSON' }] }],
        },
      },
    })

    expect(wrapper.text()).toContain('AI 返回内容格式不正确，请重新生成')
  })

  it('emits a separate event for deterministic basic positive case generation', async () => {
    const wrapper = mount(AiAssistant, { props: { selectedCount: 2, job: null } })

    await wrapper.get('[data-testid="generate-basic-positive"]').trigger('click')

    expect(wrapper.emitted('generate-basic')).toEqual([[]])
  })

  it('labels intent presets clearly and exposes every generated case', async () => {
    const wrapper = mount(AiAssistant, { props: { selectedCount: 1, job, generatedCases } })

    expect(wrapper.text()).toContain('意图模板')
    expect(wrapper.text()).toContain('接口合同与风险')
    expect(wrapper.text()).toContain('已生成用例 2')
    expect(wrapper.text()).toContain('正常分页')
    expect(wrapper.text()).toContain('页码边界')

    await wrapper.get('[data-testid="ai-generated-open-version-2"]').trigger('click')
    expect(wrapper.emitted('open-generated')).toEqual([[generatedCases[1]]])
  })
})
