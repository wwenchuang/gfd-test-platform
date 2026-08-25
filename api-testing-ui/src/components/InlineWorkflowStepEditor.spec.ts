// @vitest-environment jsdom

import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'

import type { ApiEndpoint, InlineWorkflowStep } from '../api/contracts'
import InlineWorkflowStepEditor from './InlineWorkflowStepEditor.vue'

const ENDPOINTS: ApiEndpoint[] = [
  { id: 'device-list', method: 'GET', path: '/devices/list', summary: '查询设备列表', tags: ['家用业务', '设备'] },
  { id: 'model-list', method: 'POST', path: '/models/page', summary: '查询模型列表', tags: ['家用业务', '模型'] },
]

const STEP: InlineWorkflowStep = {
  name: '查询设备列表', enabled: true,
  request: { method: 'GET', path: '/devices/list', service: 'default', path_params: {}, query: {}, headers: {}, cookies: {}, body: null },
  assertions: [], extractions: [], required_variables: [],
}

describe('InlineWorkflowStepEditor', () => {
  it('reselects a configured endpoint through the searchable endpoint picker', async () => {
    const wrapper = mount(InlineWorkflowStepEditor, {
      props: { modelValue: [STEP], stage: 'setup', endpointOptions: ENDPOINTS },
    })

    expect(wrapper.find('[data-testid="setup-endpoint-0"]').exists()).toBe(false)
    expect(wrapper.text()).toContain('GET /devices/list')

    await wrapper.get('[data-testid="setup-reselect-endpoint-0"]').trigger('click')
    expect(wrapper.find('[data-testid="endpoint-picker-search"]').exists()).toBe(true)
    expect(wrapper.find('[data-testid="endpoint-picker-manual"]').exists()).toBe(false)
    await wrapper.get('[data-testid="endpoint-picker-search"]').setValue('模型列表')
    await wrapper.get('[data-testid="endpoint-picker-option-model-list"]').trigger('click')

    const steps = wrapper.emitted('update:modelValue')?.at(-1)?.[0] as InlineWorkflowStep[]
    expect(steps[0]).toMatchObject({
      name: '查询模型列表',
      request: { method: 'POST', path: '/models/page' },
    })
  })
})
