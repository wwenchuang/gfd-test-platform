// @vitest-environment jsdom

import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'

import type { WorkflowStepPreview } from '../api/contracts'
import WorkflowStepPreviewPanel from './WorkflowStepPreviewPanel.vue'

const PREVIEW: WorkflowStepPreview = {
  status: 'PASSED', failure_category: '', error_message: '', trace: [], response: {},
  target_index: 0, executed_index: 0, target_reached: true,
  truncated: false, available_variables: [], missing_variables: [],
  fields: [
    { id: 'json_path:$.data.token', source: 'json_path', path: '$.data.token', name: 'token', value: 'secret', value_type: 'string', sensitive: true, suggested_target: 'token' },
    { id: 'json_path:$.data.userSn', source: 'json_path', path: '$.data.userSn', name: 'userSn', value: 'user-1', value_type: 'string', sensitive: false, suggested_target: 'userSn' },
  ],
}

describe('WorkflowStepPreviewPanel', () => {
  it('filters fields by path or value', async () => {
    const wrapper = mount(WorkflowStepPreviewPanel, { props: { preview: PREVIEW, extractions: [], stepName: '登录' } })

    await wrapper.get('[data-testid="workflow-preview-search"]').setValue('user-1')

    expect(wrapper.text()).toContain('$.data.userSn')
    expect(wrapper.text()).not.toContain('$.data.token')
  })

  it('blocks invalid and duplicate output variable names before applying', async () => {
    const wrapper = mount(WorkflowStepPreviewPanel, { props: { preview: PREVIEW, extractions: [], stepName: '登录' } })
    await wrapper.get('[data-testid="workflow-preview-select-json_path:$.data.token"]').setValue(true)
    await wrapper.get('[data-testid="workflow-preview-select-json_path:$.data.userSn"]').setValue(true)
    await wrapper.get('[data-testid="workflow-preview-target-json_path:$.data.token"]').setValue('1token')

    expect(wrapper.text()).toContain('变量名需以字母或下划线开头')
    expect(wrapper.get('[data-testid="workflow-preview-apply"]').attributes('disabled')).toBeDefined()

    await wrapper.get('[data-testid="workflow-preview-target-json_path:$.data.token"]').setValue('shared')
    await wrapper.get('[data-testid="workflow-preview-target-json_path:$.data.userSn"]').setValue('shared')

    expect(wrapper.text()).toContain('变量名重复')
    expect(wrapper.get('[data-testid="workflow-preview-apply"]').attributes('disabled')).toBeDefined()
  })
})
