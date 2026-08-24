// @vitest-environment jsdom

import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'

import type { ExecutionCaseResult } from '../api/contracts'
import CaseEvidence from './CaseEvidence.vue'

const result: ExecutionCaseResult = {
  execution_case_id: 'execution-case-1', case_version_id: 'case-version-1', endpoint_id: 'endpoint-1',
  case_name: '查询我的收藏', endpoint_summary: '我的收藏列表', method: 'POST', path: '/favorites/page',
  status: 'FAILED', failure_category: 'product_assertion', duration_ms: 120,
  sanitized_result: {
    sanitized_request: { url: 'https://example.test/favorites/page', headers: { Authorization: 'Bearer secret-token' }, body: { pageNum: 1 } },
    sanitized_response: { status_code: 200, body: { code: 4009, msg: '业务线未知' } },
    assertion_results: [
      { type: 'json_path', path: '$.code', expected: 0, actual: 4009, passed: false, message: '业务码不匹配' },
      { type: 'header', path: 'Authorization', expected: 'Bearer expected-secret', actual: 'Bearer actual-secret', passed: false, message: '鉴权头不匹配' },
    ],
    trace: [{ phase: 'request', message: '发送请求' }],
  },
}

describe('CaseEvidence', () => {
  it('shows setup, main and cleanup workflow evidence as separate stages', () => {
    const workflowResult: ExecutionCaseResult = {
      ...result,
      status: 'FAILED',
      failure_category: 'cleanup',
      sanitized_result: {
        ...result.sanitized_result,
        trace: [
          { phase: 'workflow_step', stage: 'setup', name: '查询在线设备', status: 'PASSED', request: { url: '/devices' }, response: { status_code: 200 } },
          { phase: 'workflow_step', stage: 'main', name: '主体请求', status: 'PASSED', request: { url: '/print' }, response: { status_code: 200 } },
          { phase: 'workflow_step', stage: 'cleanup', name: '取消本次打印', status: 'FAILED', failure_category: 'product_assertion', error_message: '业务码不匹配', request: { url: '/cancel' }, response: { status_code: 200 } },
        ],
      },
    }

    const wrapper = mount(CaseEvidence, { props: { result: workflowResult } })

    expect(wrapper.get('[data-testid="workflow-evidence"]').text()).toContain('前置步骤')
    expect(wrapper.get('[data-testid="workflow-evidence"]').text()).toContain('主体请求')
    expect(wrapper.get('[data-testid="workflow-evidence"]').text()).toContain('清理步骤')
    expect(wrapper.get('[data-testid="workflow-evidence"]').text()).toContain('取消本次打印')
    expect(wrapper.get('[data-testid="workflow-evidence"]').text()).toContain('业务码不匹配')
  })

  it('shows readable request, response, assertion and trace evidence without secrets', () => {
    const wrapper = mount(CaseEvidence, { props: { result } })

    expect(wrapper.text()).toContain('POST /favorites/page')
    expect(wrapper.text()).toContain('https://example.test/favorites/page')
    expect(wrapper.text()).toContain('HTTP 200')
    expect(wrapper.text()).toContain('业务码不匹配')
    expect(wrapper.text()).toContain('发送请求')
    expect(wrapper.text()).not.toContain('secret-token')
    expect(wrapper.text()).not.toContain('expected-secret')
    expect(wrapper.text()).not.toContain('actual-secret')
    expect(wrapper.text()).toContain('已隐藏')
  })

  it('shows the attempt number for polled workflow steps', () => {
    const workflowResult: ExecutionCaseResult = {
      ...result,
      sanitized_result: {
        ...result.sanitized_result,
        trace: [
          { phase: 'workflow_step', stage: 'setup', name: '等待图片生成', status: 'FAILED', attempt: 1, max_attempts: 3, request: {}, response: {} },
          { phase: 'workflow_step', stage: 'setup', name: '等待图片生成', status: 'PASSED', attempt: 2, max_attempts: 3, request: {}, response: {} },
        ],
      },
    }

    const wrapper = mount(CaseEvidence, { props: { result: workflowResult } })

    expect(wrapper.get('[data-testid="workflow-evidence"]').text()).toContain('第 1/3 次')
    expect(wrapper.get('[data-testid="workflow-evidence"]').text()).toContain('第 2/3 次')
  })

  it('emits edit and rerun commands with the selected result', async () => {
    const wrapper = mount(CaseEvidence, { props: { result } })

    await wrapper.get('[data-testid="edit-case"]').trigger('click')
    await wrapper.get('[data-testid="rerun-case"]').trigger('click')

    expect(wrapper.emitted('edit')?.[0]?.[0]).toEqual(result)
    expect(wrapper.emitted('rerun')?.[0]?.[0]).toEqual(result)
  })
})
