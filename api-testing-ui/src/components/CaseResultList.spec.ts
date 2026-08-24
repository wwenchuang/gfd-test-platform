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
})
