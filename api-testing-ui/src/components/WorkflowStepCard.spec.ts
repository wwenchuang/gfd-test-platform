// @vitest-environment jsdom

import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'

import WorkflowStepCard from './WorkflowStepCard.vue'
import type { InlineWorkflowStep } from '../api/contracts'

const STEP: InlineWorkflowStep = {
  name: '查询模型', enabled: true,
  request: { method: 'GET', path: '/resource/page', service: 'default', path_params: {}, query: { page: 1 }, headers: {}, cookies: {}, body: null },
  assertions: [{ type: 'status_code', operator: 'equals', expected: 200, enabled: true }],
  extractions: [{ target: 'modelSn', type: 'json_path', path: '$.data.modelSn' }],
  required_variables: [],
}

describe('WorkflowStepCard', () => {
  it('renders a useful summary while its body is collapsed', () => {
    const wrapper = mount(WorkflowStepCard, {
      props: { step: STEP, index: 0, stage: 'setup', active: false, issueCount: 1, first: true, last: true },
      slots: { default: '<div data-testid="body-content">正文</div>' },
    })

    expect(wrapper.text()).toContain('GET /resource/page')
    expect(wrapper.text()).toContain('参数 1')
    expect(wrapper.text()).toContain('断言 1')
    expect(wrapper.text()).toContain('提取 1')
    expect(wrapper.text()).toContain('错误 1')
    expect(wrapper.find('[data-testid="body-content"]').exists()).toBe(false)
  })

  it('emits summary actions without owning workflow mutations', async () => {
    const wrapper = mount(WorkflowStepCard, {
      props: { step: STEP, index: 1, stage: 'setup', active: true, issueCount: 0, first: false, last: false },
    })

    await wrapper.get('[data-testid="setup-step-toggle-1"]').trigger('click')
    await wrapper.get('[data-testid="setup-step-up-1"]').trigger('click')
    await wrapper.get('[data-testid="setup-step-duplicate-1"]').trigger('click')

    expect(wrapper.emitted('toggle')).toHaveLength(1)
    expect(wrapper.emitted('move')?.[0]).toEqual([-1])
    expect(wrapper.emitted('duplicate')).toHaveLength(1)
  })
})
