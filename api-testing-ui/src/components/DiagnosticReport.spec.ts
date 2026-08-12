// @vitest-environment jsdom

import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'

import type { ExecutionView } from '../api/contracts'
import DiagnosticReport from './DiagnosticReport.vue'

const execution: ExecutionView = {
  id: 'execution-report-1', project_id: 'project-1', state: 'DONE', execution_type: 'regression',
  source_revision_id: 'source-1', environment_revision_id: 'environment-1', environment_name: '生产环境 V6',
  case_statuses: ['PASSED', 'FAILED', 'BROKEN', 'SKIPPED'], summary: { total: 4, passed: 1, failed: 1, broken: 1, skipped: 1 }, cancellation_requested: false,
  created_at: '2026-08-12T07:00:00Z', started_at: '2026-08-12T07:00:01Z', finished_at: '2026-08-12T07:00:03Z',
  case_results: [
    { execution_case_id: 'case-1', case_version_id: 'version-1', endpoint_id: 'endpoint-1', case_name: '查询收藏', endpoint_summary: '', method: 'POST', path: '/favorites/page', status: 'PASSED', failure_category: '', duration_ms: 100, sanitized_result: {} },
    { execution_case_id: 'case-2', case_version_id: 'version-2', endpoint_id: 'endpoint-2', case_name: '取消收藏', endpoint_summary: '', method: 'POST', path: '/favorites/cancel', status: 'FAILED', failure_category: 'product_assertion', duration_ms: 200, sanitized_result: { assertion_results: [{ passed: false, message: '业务码不匹配' }] }, failure_analysis: { analyzer: 'ai_gateway', model: 'qwen-plus', category: 'product_assertion', analysis: { summary: '业务码异常', root_cause: '响应 code=4009', recommendations: ['核对业务线'], evidence: ['实际响应不符合断言'] } } },
    { execution_case_id: 'case-3', case_version_id: 'version-3', endpoint_id: 'endpoint-3', case_name: '添加收藏', endpoint_summary: '', method: 'POST', path: '/favorites/add', status: 'BROKEN', failure_category: 'environment', duration_ms: 50, sanitized_result: { error_message: '连接超时', trace: [{ headers: { Authorization: 'Bearer trace-secret' } }] } },
    { execution_case_id: 'case-4', case_version_id: 'version-4', endpoint_id: 'endpoint-4', case_name: '批量收藏', endpoint_summary: '', method: 'POST', path: '/favorites/batch', status: 'SKIPPED', failure_category: 'dependency', duration_ms: 0, sanitized_result: { skip_reason: '前置失败' } },
  ],
}

describe('DiagnosticReport', () => {
  it('keeps deterministic child states and renders diagnostic categories and AI evidence', () => {
    const wrapper = mount(DiagnosticReport, { props: { execution } })

    expect(wrapper.get('[data-testid="execution-conclusion"]').text()).toBe('未通过')
    expect(wrapper.text()).toContain('查询收藏')
    expect(wrapper.text()).toContain('通过')
    expect(wrapper.text()).toContain('产品失败')
    expect(wrapper.text()).toContain('环境异常')
    expect(wrapper.text()).toContain('依赖跳过')
    expect(wrapper.text()).toContain('AI 诊断摘要')
    expect(wrapper.text()).toContain('业务码异常')
    expect(wrapper.text()).toContain('技术日志')
    expect(wrapper.text()).not.toContain('trace-secret')
    expect(wrapper.text()).toContain('已取消')
    expect(wrapper.text()).toContain('POST /favorites/add')
    expect(wrapper.text()).toContain('50 ms')
  })

  it('switches evidence without changing the passed child status', async () => {
    const wrapper = mount(DiagnosticReport, { props: { execution } })
    await wrapper.findAll('[data-testid="report-case-row"]')[0].trigger('click')

    expect(wrapper.text()).toContain('请求和断言均通过')
    expect(wrapper.text()).toContain('查询收藏')
    expect(wrapper.find('.case-evidence [data-testid="rerun-case"]').exists()).toBe(false)
  })

  it('filters failed, broken and skipped cases without changing their states', async () => {
    const wrapper = mount(DiagnosticReport, { props: { execution } })

    await wrapper.get('[data-testid="report-filter-broken"]').trigger('click')
    const rows = wrapper.findAll('[data-testid="report-case-row"]')

    expect(rows).toHaveLength(1)
    expect(rows[0].text()).toContain('添加收藏')
    expect(rows[0].text()).toContain('运行异常')
  })
})
