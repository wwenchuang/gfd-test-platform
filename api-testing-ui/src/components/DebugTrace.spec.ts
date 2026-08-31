// @vitest-environment jsdom

import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'

import DebugTrace from './DebugTrace.vue'
import type { DebugTraceStep } from '../api/contracts'

const TRACE: DebugTraceStep[] = [
  { stage: 'setup', index: 0, name: '查询模型', status: 'PASSED', failureCategory: '', assertions: [], extractedVariableNames: ['modelSn'], missingVariableNames: [], request: {}, response: {}, error: '', attempt: 1, maxAttempts: 1 },
  { stage: 'main', index: 0, name: '下发打印', status: 'PASSED', failureCategory: '', assertions: [{ passed: true }], extractedVariableNames: ['printTaskSn'], missingVariableNames: [], request: {}, response: {}, error: '', attempt: 1, maxAttempts: 1 },
  { stage: 'cleanup', index: 0, name: '取消打印', status: 'FAILED', failureCategory: 'cleanup', assertions: [], extractedVariableNames: [], missingVariableNames: [], request: {}, response: {}, error: '取消打印失败', attempt: 2, maxAttempts: 2 },
]

describe('DebugTrace', () => {
  it('shows setup main and cleanup status before raw evidence', async () => {
    const wrapper = mount(DebugTrace, { props: { trace: TRACE } })
    expect(wrapper.findAll('.trace-content header b').map(item => item.text())).toEqual(['通过', '通过', '未通过'])
    expect(wrapper.text()).toContain('前置步骤')
    expect(wrapper.text()).toContain('主体请求')
    expect(wrapper.text()).toContain('清理步骤')
    expect(wrapper.text()).toContain('取消打印失败')
    expect(wrapper.text()).toContain('printTaskSn')
    await wrapper.get('[data-testid="edit-debug-step-cleanup-0"]').trigger('click')
    expect(wrapper.emitted('edit-step')?.[0]?.[0]).toEqual({ stage: 'cleanup', index: 0 })
  })
})
