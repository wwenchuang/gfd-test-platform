// @vitest-environment jsdom

import { mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { apiClient } from '../api/client'
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
  beforeEach(() => {
    vi.restoreAllMocks()
  })

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

  it('runs through the selected setup step and converts selected response fields into extractions', async () => {
    const post = vi.spyOn(apiClient, 'post').mockResolvedValue({
      data: {
        preview: {
          status: 'PASSED', failure_category: '', error_message: '', trace: [],
          target_index: 0, executed_index: 0, target_reached: true,
          response: { status_code: 200, body: { data: { access_token: 'real-token' } } },
          fields: [{
            id: 'json_path:$.data.access_token', source: 'json_path', path: '$.data.access_token',
            name: 'access_token', value: 'real-token', value_type: 'string', sensitive: true,
            suggested_target: 'access_token',
          }],
          truncated: false, available_variables: [], missing_variables: [],
        },
      },
      request_id: 'request-1',
    })
    const wrapper = mount(InlineWorkflowStepEditor, {
      props: {
        modelValue: [STEP], stage: 'setup', endpointOptions: ENDPOINTS,
        environmentRevisionId: 'environment-revision-1',
        initialVariables: { seed: 'value' }, processingPre: [],
      },
    })

    await wrapper.get('[data-testid="setup-preview-0"]').trigger('click')

    expect(post).toHaveBeenCalledWith('/api/api-testing/v1/workflow-steps/preview', {
      environment_revision_id: 'environment-revision-1',
      setup_steps: [STEP], target_index: 0,
      initial_variables: { seed: 'value' }, processing_pre: [], extraction_overrides: {},
    })
    expect(wrapper.get('[data-testid="workflow-preview-sensitive-json_path:$.data.access_token"]').attributes('type')).toBe('password')
    expect(wrapper.text()).toContain('已到达第 1 步')
    await wrapper.get('[data-testid="workflow-preview-reveal-json_path:$.data.access_token"]').trigger('click')
    expect(wrapper.get('[data-testid="workflow-preview-sensitive-json_path:$.data.access_token"]').attributes('type')).toBe('text')
    await wrapper.get('[data-testid="workflow-preview-select-json_path:$.data.access_token"]').setValue(true)
    await wrapper.get('[data-testid="workflow-preview-target-json_path:$.data.access_token"]').setValue('ZXBToken')
    await wrapper.get('[data-testid="workflow-preview-apply"]').trigger('click')

    const steps = wrapper.emitted('update:modelValue')?.at(-1)?.[0] as InlineWorkflowStep[]
    expect(steps[0].extractions).toEqual([{ target: 'ZXBToken', type: 'json_path', path: '$.data.access_token', required: true }])
  })

  it('keeps an edited token as a session-only override for later setup previews', async () => {
    const post = vi.spyOn(apiClient, 'post').mockResolvedValue({
      data: {
        preview: {
          status: 'PASSED', failure_category: '', error_message: '', trace: [], response: {},
          target_index: 0, executed_index: 0, target_reached: true,
          fields: [{
            id: 'json_path:$.data.access_token', source: 'json_path', path: '$.data.access_token',
            name: 'access_token', value: 'real-token', value_type: 'string', sensitive: true,
            suggested_target: 'accessToken',
          }],
          truncated: false, available_variables: ['accessToken'], missing_variables: [],
        },
      },
      request_id: 'request-1',
    })
    const first = { ...STEP, extractions: [{ target: 'accessToken', type: 'json_path', path: '$.data.access_token', required: true }] }
    const second = { ...STEP, name: '查询设备', request: { ...STEP.request, path: '/devices/list', headers: { Authorization: 'Bearer {{accessToken}}' } }, required_variables: ['accessToken'] }
    const wrapper = mount(InlineWorkflowStepEditor, {
      props: { modelValue: [first, second], stage: 'setup', environmentRevisionId: 'environment-revision-1' },
    })

    await wrapper.get('[data-testid="setup-preview-0"]').trigger('click')
    await wrapper.get('[data-testid="workflow-preview-sensitive-json_path:$.data.access_token"]').setValue('replacement-token')
    await wrapper.get('[data-testid="workflow-preview-select-json_path:$.data.access_token"]').setValue(true)
    await wrapper.get('[data-testid="workflow-preview-apply"]').trigger('click')
    await wrapper.get('[data-testid="setup-step-toggle-1"]').trigger('click')
    await wrapper.get('[data-testid="setup-preview-1"]').trigger('click')

    expect(post).toHaveBeenLastCalledWith('/api/api-testing/v1/workflow-steps/preview', expect.objectContaining({
      target_index: 1,
      extraction_overrides: { accessToken: 'replacement-token' },
    }))
    const emitted = wrapper.emitted('update:modelValue')?.at(-1)?.[0] as InlineWorkflowStep[]
    expect(JSON.stringify(emitted)).not.toContain('replacement-token')
  })
})
