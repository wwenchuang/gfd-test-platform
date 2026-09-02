// @vitest-environment jsdom

import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'

import type { ExecutionCaseResult } from '../api/contracts'
import FailureAnalysis from './FailureAnalysis.vue'

const failed: ExecutionCaseResult = {
  execution_case_id: 'execution-case-1', case_version_id: 'case-version-1', endpoint_id: 'endpoint-1',
  case_name: '取消收藏', endpoint_summary: '取消收藏', method: 'POST', path: '/favorites/cancel',
  status: 'FAILED', failure_category: 'product_assertion', duration_ms: 80,
  sanitized_result: { assertion_results: [{ passed: false, message: '$.code 期望 0，实际 4009' }] },
}

describe('FailureAnalysis', () => {
  it('shows persisted AI evidence and the actual model', () => {
    const wrapper = mount(FailureAnalysis, { props: { result: {
      ...failed,
      failure_analysis: {
        analyzer: 'ai_gateway', model: 'qwen3.7-plus', category: 'product_assertion',
        analysis: {
          summary: '收藏接口业务码异常', root_cause: '服务返回 code=4009',
          recommendations: ['核对收藏对象状态'], evidence: ['HTTP 200，但业务码失败'],
        },
      },
    } } })

    expect(wrapper.text()).toContain('AI 失败分析')
    expect(wrapper.text()).toContain('qwen3.7-plus')
    expect(wrapper.text()).toContain('收藏接口业务码异常')
    expect(wrapper.text()).toContain('HTTP 200，但业务码失败')
  })

  it('labels deterministic fallback guidance as platform diagnosis', () => {
    const wrapper = mount(FailureAnalysis, { props: { result: failed } })

    expect(wrapper.text()).toContain('平台诊断')
    expect(wrapper.text()).not.toContain('AI 失败分析')
  })

  it('translates missing environment variables and tells the user where to fix them', () => {
    const wrapper = mount(FailureAnalysis, { props: { result: {
      ...failed,
      status: 'BROKEN',
      failure_category: 'environment',
      sanitized_result: { error_message: 'undefined environment variable: sessionId' },
    } } })

    expect(wrapper.text()).toContain('环境变量“sessionId”未配置')
    expect(wrapper.text()).toContain('到“环境配置”补充变量 sessionId')
    expect(wrapper.text()).not.toContain('undefined environment variable')
  })
})
