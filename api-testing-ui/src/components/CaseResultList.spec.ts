// @vitest-environment jsdom

import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'

import CaseResultList from './CaseResultList.vue'
import type { ExecutionCaseResult } from '../api/contracts'

describe('CaseResultList', () => {
  it('marks automatically expanded dependency cases', () => {
    const result = {
      execution_case_id: 'execution-case-1',
      case_version_id: 'case-version-1',
      endpoint_id: 'endpoint-1',
      execution_role: 'dependency',
      case_name: '添加收藏',
      endpoint_summary: '',
      method: 'POST',
      path: '/collection/add',
      status: 'PASSED',
      failure_category: '',
      duration_ms: 12,
      sanitized_result: {},
    } as ExecutionCaseResult

    const wrapper = mount(CaseResultList, { props: { results: [result] } })

    expect(wrapper.get('[data-testid="dependency-role"]').text()).toBe('前置')
  })

  it('keeps large execution details searchable and paginated instead of rendering every case', async () => {
    const results = Array.from({ length: 51 }, (_, index) => ({
      execution_case_id: `execution-case-${index + 1}`,
      case_version_id: `case-version-${index + 1}`,
      endpoint_id: `endpoint-${index + 1}`,
      case_name: `回归用例 ${index + 1}`,
      endpoint_summary: '',
      method: 'GET',
      path: `/cases/${index + 1}`,
      status: 'PASSED',
      failure_category: '',
      duration_ms: 10,
      sanitized_result: {},
    })) as ExecutionCaseResult[]
    const wrapper = mount(CaseResultList, { props: { results } })

    expect(wrapper.findAll('[data-testid="case-result-row"]')).toHaveLength(50)
    expect(wrapper.text()).toContain('第 1-50 条，共 51 条')

    await wrapper.get('[data-testid="case-result-search"]').setValue('回归用例 51')
    expect(wrapper.findAll('[data-testid="case-result-row"]')).toHaveLength(1)
    expect(wrapper.text()).toContain('/cases/51')

    await wrapper.get('[data-testid="case-result-search"]').setValue('')
    await wrapper.get('[data-testid="case-result-next"]').trigger('click')
    expect(wrapper.findAll('[data-testid="case-result-row"]')).toHaveLength(1)
    expect(wrapper.text()).toContain('第 51-51 条，共 51 条')
  })

  it('opens the page containing the active case so list and evidence stay aligned', async () => {
    const results = Array.from({ length: 51 }, (_, index) => ({
      execution_case_id: `execution-case-${index + 1}`,
      case_version_id: `case-version-${index + 1}`,
      endpoint_id: `endpoint-${index + 1}`,
      case_name: `回归用例 ${index + 1}`,
      endpoint_summary: '',
      method: 'GET',
      path: `/cases/${index + 1}`,
      status: 'PASSED',
      failure_category: '',
      duration_ms: 10,
      sanitized_result: {},
    })) as ExecutionCaseResult[]

    const wrapper = mount(CaseResultList, {
      props: { results, activeId: 'execution-case-51' },
    })

    expect(wrapper.findAll('[data-testid="case-result-row"]')).toHaveLength(1)
    expect(wrapper.get('[data-testid="case-result-row"]').classes()).toContain('active')
    expect(wrapper.text()).toContain('第 51-51 条，共 51 条')

    await wrapper.get('[data-testid="case-result-search"]').setValue('回归用例 1')
    await wrapper.get('[data-testid="case-result-search"]').setValue('')
    expect(wrapper.get('[data-testid="case-result-row"]').classes()).toContain('active')
    expect(wrapper.text()).toContain('第 51-51 条，共 51 条')
  })
})
