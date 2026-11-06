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
  it('shows unconfirmed cleanup for the persisted interrupted worker result', () => {
    const wrapper = mount(CaseEvidence, { props: { result: {
      ...result, status: 'BROKEN', failure_category: 'worker', evidence_loaded: true,
      sanitized_result: { status: 'BROKEN', failure_category: 'worker', error_message: '执行 Worker 中断；已完成结果已保留，未完成用例未自动重放', trace: [{ phase: 'failure', status: 'BROKEN', failure_category: 'worker' }] },
    } } })
    expect(wrapper.get('[data-testid="cleanup-evidence-warning"]').text()).toContain('无法确认是否需要清理或清理是否完成')
    expect(wrapper.get('[data-testid="cleanup-evidence-warning"]').text()).not.toContain('已产生数据')
  })

  it('shows an unknown cleanup state for cancellation recovery without evidence', () => {
    const wrapper = mount(CaseEvidence, { props: { result: {
      ...result, status: 'CANCELLED', failure_category: 'cancelled', evidence_loaded: false, sanitized_result: {},
    } } })
    expect(wrapper.get('[data-testid="cleanup-evidence-warning"]').text()).toContain('无法确认是否需要清理或清理是否完成')
    expect(wrapper.get('[data-testid="cleanup-evidence-warning"]').text()).not.toContain('已产生数据')
    expect(wrapper.text()).toContain('未读取到当前用例')
  })

  it('does not announce missing evidence while a cancellation detail is loading', () => {
    const wrapper = mount(CaseEvidence, { props: { loading: true, result: {
      ...result, status: 'CANCELLED', failure_category: 'cancelled', evidence_loaded: false, sanitized_result: {},
    } } })
    expect(wrapper.find('[data-testid="cleanup-evidence-warning"]').exists()).toBe(false)
  })

  it('uses cleanup step evidence when a cancelled case actually has it', () => {
    const wrapper = mount(CaseEvidence, { props: { result: {
      ...result, status: 'CANCELLED', failure_category: 'cancelled', evidence_loaded: true,
      sanitized_result: { trace: [{ phase: 'workflow_step', stage: 'cleanup', index: 0, status: 'PASSED' }] },
    } } })
    expect(wrapper.find('[data-testid="cleanup-evidence-warning"]').exists()).toBe(false)
    expect(wrapper.get('[data-testid="cleanup-evidence-passed"]').text()).toContain('实际资源状态仍需核对')
  })

  it('highlights a cleanup failure independently of the main failure', () => {
    const wrapper = mount(CaseEvidence, { props: { result: { ...result, sanitized_result: {
      ...result.sanitized_result, trace: [
        { phase: 'workflow_step', stage: 'main', index: 0, status: 'FAILED' },
        { phase: 'workflow_step', stage: 'cleanup', index: 0, status: 'FAILED' },
      ],
    } } } })
    expect(wrapper.get('[data-testid="cleanup-evidence-warning"]').text()).toContain('清理步骤未通过')
    expect(wrapper.text()).toContain('业务码不匹配')
  })

  it('uses the final attempt for each cleanup step and does not call resources deleted', () => {
    const wrapper = mount(CaseEvidence, { props: { result: { ...result, sanitized_result: {
      ...result.sanitized_result, trace: [
        { phase: 'workflow_step', stage: 'cleanup', index: 0, status: 'FAILED', attempt: 1, max_attempts: 2 },
        { phase: 'workflow_step', stage: 'cleanup', index: 0, status: 'PASSED', attempt: 2, max_attempts: 2 },
      ],
    } } } })
    expect(wrapper.find('[data-testid="cleanup-evidence-warning"]').exists()).toBe(false)
    expect(wrapper.get('[data-testid="cleanup-evidence-passed"]').text()).toContain('清理步骤已记录为通过')
    expect(wrapper.get('[data-testid="cleanup-evidence-passed"]').text()).toContain('实际资源状态仍需核对')
  })

  it('marks skipped cleanup as unconfirmed, even when other cleanup steps passed', () => {
    const wrapper = mount(CaseEvidence, { props: { result: { ...result, sanitized_result: {
      ...result.sanitized_result, trace: [
        { phase: 'workflow_step', stage: 'cleanup', index: 0, status: 'PASSED' },
        { phase: 'workflow_step', stage: 'cleanup', index: 1, status: 'SKIPPED', failure_category: 'condition_not_met' },
      ],
    } } } })
    expect(wrapper.get('[data-testid="cleanup-evidence-warning"]').text()).toContain('清理步骤被跳过')
    expect(wrapper.find('[data-testid="cleanup-evidence-passed"]').exists()).toBe(false)
  })

  it('keeps missing-variable cleanup and unrecognized statuses unconfirmed', () => {
    for (const step of [
      { phase: 'workflow_step', stage: 'cleanup', index: 0, status: 'SKIPPED', failure_category: 'missing_variables' },
      { phase: 'workflow_step', stage: 'cleanup', index: 0, status: 'UNKNOWN' },
    ]) {
      const wrapper = mount(CaseEvidence, { props: { result: { ...result, sanitized_result: { ...result.sanitized_result, trace: [step] } } } })
      expect(wrapper.get('[data-testid="cleanup-evidence-warning"]').text()).toMatch(/清理步骤被跳过|当前未能确认清理步骤证据/)
      expect(wrapper.find('[data-testid="cleanup-evidence-passed"]').exists()).toBe(false)
    }
  })

  it('does not invent cleanup status for read-only and historical records without cleanup evidence', () => {
    for (const status of ['PASSED', 'FAILED', 'BROKEN']) {
      const wrapper = mount(CaseEvidence, { props: { result: { ...result, method: 'GET', status, sanitized_result: { ...result.sanitized_result, trace: [] } } } })
      expect(wrapper.find('[data-testid="cleanup-evidence-warning"]').exists()).toBe(false)
      expect(wrapper.find('[data-testid="cleanup-evidence-passed"]').exists()).toBe(false)
    }
  })

  it('labels a cleanup-category legacy record with no step details as unconfirmed', () => {
    const wrapper = mount(CaseEvidence, { props: { result: { ...result, failure_category: 'cleanup', sanitized_result: { ...result.sanitized_result, trace: [] } } } })
    expect(wrapper.get('[data-testid="cleanup-evidence-warning"]').text()).toContain('当前未能确认清理步骤证据')
  })

  it('shows an advisory warning without replacing the functional result', () => {
    const wrapper = mount(CaseEvidence, { props: { result: { ...result, status: 'PASSED', sanitized_result: { ...result.sanitized_result, response_time_warning: { status: 'slow', duration_ms: 1200, threshold_ms: 1000 } } } } })
    expect(wrapper.get('[data-testid="response-time-slow"]').text()).toContain('1200 ms')
    expect(wrapper.get('[data-testid="response-time-slow"]').text()).toContain('不受影响')
    expect(wrapper.text()).toContain('通过')
  })
  it('distinguishes the main response time from the full workflow duration', () => {
    const wrapper = mount(CaseEvidence, { props: { result: { ...result, duration_ms: 1200, sanitized_result: { ...result.sanitized_result, sanitized_response: { status_code: 200, duration_ms: 80 } } } } })
    expect(wrapper.get('[data-testid="response-duration"]').text()).toContain('80 ms')
    expect(wrapper.get('[data-testid="workflow-duration"]').text()).toContain('1200 ms')
  })

  it('does not invent a response duration when no response arrived', () => {
    const wrapper = mount(CaseEvidence, { props: { result: { ...result, sanitized_result: { ...result.sanitized_result, sanitized_response: {} } } } })
    expect(wrapper.get('[data-testid="response-duration"]').text()).toContain('未记录')
  })

  it('does not display a failed setup response as the main response', () => {
    const wrapper = mount(CaseEvidence, { props: { result: { ...result, failure_category: 'setup', sanitized_result: { ...result.sanitized_result, sanitized_response: { status_code: 500, duration_ms: 300 } } } } })
    expect(wrapper.get('[data-testid="response-duration"]').text()).toContain('未记录')
  })
  it('explains deferred evidence loading and provides a retry action after failure', async () => {
    const deferredResult = { ...result, evidence_loaded: false, sanitized_result: {} }
    const wrapper = mount(CaseEvidence, { props: { result: deferredResult, error: '证据读取超时' } })

    expect(wrapper.text()).toContain('证据读取超时')
    await wrapper.get('[data-testid="retry-case-evidence"]').trigger('click')
    expect(wrapper.emitted('retry')).toHaveLength(1)

    await wrapper.setProps({ error: '', loading: true })
    expect(wrapper.text()).toContain('正在读取当前用例的请求、响应和断言')
    expect(wrapper.find('details').exists()).toBe(false)
  })

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

    expect(wrapper.findAll('.workflow-evidence-head b').map(item => item.text())).toEqual(['通过', '通过', '未通过'])
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

  it('keeps oversized response and computed assertion values compact until each is explicitly expanded', async () => {
    const responseTailMarker = 'RESPONSE_TAIL_MUST_BE_LAZY'
    const assertionTailMarker = 'COMPUTED_ASSERTION_TAIL_MUST_BE_ACCESSIBLE'
    const oversizedResponse = `${'x'.repeat(18_000)}${responseTailMarker}`
    const computedAssertion = `${'y'.repeat(18_000)}${assertionTailMarker}`
    const largeResult: ExecutionCaseResult = {
      ...result,
      evidence_loaded: true,
      sanitized_result: {
        ...result.sanitized_result,
        sanitized_response: { status_code: 200, body: { payload: oversizedResponse } },
        assertion_results: [{ passed: true, message: '计算字段符合预期', expected: '存在计算结果', actual: computedAssertion }],
      },
    }
    const wrapper = mount(CaseEvidence, { props: { result: largeResult } })

    expect(wrapper.get('[data-testid="response-evidence"]').attributes('open')).toBeUndefined()
    expect(wrapper.get('[data-testid="response-evidence"]').text()).toContain('默认收起')
    expect(wrapper.text()).toContain('内容较大，已先展示前')
    expect(wrapper.get('[data-testid="assertion-actual-0"]').text()).toContain('实际y')
    expect(wrapper.text()).toContain('已截断')
    expect(wrapper.text()).not.toContain(responseTailMarker)
    expect(wrapper.text()).not.toContain(assertionTailMarker)

    await wrapper.get('[data-testid="expand-assertion-actual-0"]').trigger('click')
    expect(wrapper.text()).toContain(assertionTailMarker)
    expect(wrapper.text()).not.toContain(responseTailMarker)
    expect(wrapper.get('[data-testid="expand-assertion-actual-0"]').text()).toContain('收起完整值')

    await wrapper.get('[data-testid="expand-assertion-actual-0"]').trigger('click')
    expect(wrapper.text()).not.toContain(assertionTailMarker)

    await wrapper.get('[data-testid="expand-response-evidence"]').trigger('click')
    expect(wrapper.text()).toContain(responseTailMarker)
    expect(wrapper.get('[data-testid="expand-response-evidence"]').text()).toContain('恢复精简预览')

    await wrapper.get('[data-testid="expand-response-evidence"]').trigger('click')
    expect(wrapper.text()).not.toContain(responseTailMarker)
  })

  it('keeps oversized binary assertion values out of the first-screen assertion summary', () => {
    const binaryValue = `data:image/png;base64,${'A'.repeat(100_000)}`
    const binaryResult: ExecutionCaseResult = {
      ...result,
      evidence_loaded: true,
      sanitized_result: {
        ...result.sanitized_result,
        sanitized_response: { status_code: 200, body: { slideImage: binaryValue } },
        assertion_results: [{
          passed: true,
          message: '滑块图片已返回',
          expected: '存在 slideImage',
          actual: { code: 0, data: { slideImage: binaryValue } },
        }],
      },
    }

    const wrapper = mount(CaseEvidence, { props: { result: binaryResult } })
    const actual = wrapper.get('[data-testid="assertion-actual-0"]')

    expect(actual.text()).toContain('实际')
    expect(actual.text()).toContain('已截断')
    expect(actual.text()).not.toContain('完整值请在上方响应明细查看')
    expect(wrapper.get('[data-testid="expand-assertion-actual-0"]').text()).toContain('显示完整值')
    expect(actual.text().length).toBeLessThan(360)
    expect(actual.text()).not.toContain('A'.repeat(200))
  })

  it('keeps ordinary responses open for quick inspection', () => {
    const wrapper = mount(CaseEvidence, { props: { result } })

    expect(wrapper.get('[data-testid="response-evidence"]').attributes('open')).toBe('')
    expect(wrapper.get('[data-testid="response-evidence"]').text()).not.toContain('默认收起')
  })
})
