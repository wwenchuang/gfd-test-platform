// @vitest-environment jsdom

import { mount } from '@vue/test-utils'
import { afterEach, describe, expect, it } from 'vitest'
import { nextTick } from 'vue'

import type { ExecutionView } from '../api/contracts'
import ExecutionDetailDrawer from './ExecutionDetailDrawer.vue'

const execution: ExecutionView = {
  id: 'execution-1', project_id: 'project-1', state: 'DONE', execution_type: 'debug',
  source_revision_id: 'source-1', environment_revision_id: 'environment-1', environment_name: '生产环境',
  case_statuses: ['FAILED'], summary: { FAILED: 1 }, cancellation_requested: false,
  created_at: '2026-08-10T00:00:00Z', started_at: '2026-08-10T00:00:01Z', finished_at: '2026-08-10T00:00:02Z',
  case_results: [{
    execution_case_id: 'execution-case-1', case_version_id: 'case-version-1', endpoint_id: 'endpoint-1',
    case_name: '查询我的收藏', endpoint_summary: '我的收藏列表', method: 'GET', path: '/favorites',
    status: 'FAILED', failure_category: 'product_assertion', duration_ms: 120,
    sanitized_result: { assertion_results: [{ passed: false, message: '状态码不匹配' }] },
  }],
}

describe('ExecutionDetailDrawer', () => {
  afterEach(() => { document.body.innerHTML = '' })

  it('emits the exact case selected for editing', async () => {
    const wrapper = mount(ExecutionDetailDrawer, { props: { execution } })

    await wrapper.get('[data-testid="edit-case"]').trigger('click')

    expect(wrapper.emitted('edit')?.[0]?.[0]).toMatchObject({
      endpoint_id: 'endpoint-1', case_version_id: 'case-version-1',
    })
  })

  it('traps keyboard focus and returns it when closed', async () => {
    const opener = document.createElement('button')
    document.body.appendChild(opener)
    opener.focus()
    const wrapper = mount(ExecutionDetailDrawer, { attachTo: document.body, props: { execution } })
    await nextTick()
    await nextTick()
    const dialog = wrapper.get('[role="dialog"]')
    const buttons = dialog.findAll('button')
    buttons.at(-1)!.element.focus()

    await dialog.trigger('keydown', { key: 'Tab' })
    expect(document.activeElement).toBe(buttons[0].element)

    await dialog.trigger('keydown', { key: 'Escape' })
    expect(wrapper.emitted('close')).toHaveLength(1)
    wrapper.unmount()
    expect(document.activeElement).toBe(opener)
  })

  it('resets the active result when the execution changes', async () => {
    const wrapper = mount(ExecutionDetailDrawer, { props: { execution } })
    const nextExecution: ExecutionView = {
      ...execution,
      id: 'execution-2',
      source_revision_id: 'source-2',
      environment_revision_id: 'environment-2',
      case_results: [{
        ...execution.case_results[0], execution_case_id: 'execution-case-2', endpoint_id: 'endpoint-2',
        case_version_id: 'case-version-2', case_name: '取消收藏', path: '/favorites/cancel',
      }],
    }

    await wrapper.setProps({ execution: nextExecution })
    await wrapper.get('[data-testid="edit-case"]').trigger('click')

    expect(wrapper.emitted('edit')?.at(-1)?.[0]).toMatchObject({ endpoint_id: 'endpoint-2', case_version_id: 'case-version-2' })
    expect(wrapper.emitted('edit')?.at(-1)?.[1]).toMatchObject({ id: 'execution-2', source_revision_id: 'source-2' })
  })

  it('updates the selected result when background AI analysis arrives', async () => {
    const wrapper = mount(ExecutionDetailDrawer, { props: { execution } })
    const analyzed: ExecutionView = {
      ...execution,
      case_results: [{
        ...execution.case_results[0],
        failure_analysis: {
          analyzer: 'ai_gateway', model: 'qwen3.7-plus', category: 'product_assertion',
          analysis: { summary: '收藏接口业务码异常', root_cause: 'code=4009', recommendations: ['检查数据'], evidence: ['断言失败'] },
        },
      }],
    }

    await wrapper.setProps({ execution: analyzed })

    expect(wrapper.text()).toContain('AI 失败分析')
    expect(wrapper.text()).toContain('qwen3.7-plus')
  })

  it('opens the requested case rather than always selecting the first result', async () => {
    const second = {
      ...execution.case_results[0], execution_case_id: 'execution-case-2', endpoint_id: 'endpoint-2',
      case_version_id: 'case-version-2', case_name: '取消收藏', path: '/favorites/cancel',
    }
    const wrapper = mount(ExecutionDetailDrawer, {
      props: { execution: { ...execution, case_results: [...execution.case_results, second] }, initialCaseId: second.execution_case_id },
    })

    expect(wrapper.text()).toContain('取消收藏')
    await wrapper.get('[data-testid="edit-case"]').trigger('click')
    expect(wrapper.emitted('edit')?.[0]?.[0]).toMatchObject({ endpoint_id: 'endpoint-2' })
  })

  it('keeps the requested case visible when its result is on a later page', () => {
    const results = Array.from({ length: 51 }, (_, index) => ({
      ...execution.case_results[0],
      execution_case_id: `execution-case-${index + 1}`,
      case_version_id: `case-version-${index + 1}`,
      case_name: `回归用例 ${index + 1}`,
      path: `/cases/${index + 1}`,
    }))
    const wrapper = mount(ExecutionDetailDrawer, {
      props: {
        execution: { ...execution, case_results: results },
        initialCaseId: 'execution-case-51',
      },
    })

    expect(wrapper.get('[data-testid="case-result-row"]').classes()).toContain('active')
    expect(wrapper.text()).toContain('第 51-51 条，共 51 条')
  })

  it('requests evidence for the opened case and every newly selected case', async () => {
    const second = {
      ...execution.case_results[0], execution_case_id: 'execution-case-2', endpoint_id: 'endpoint-2',
      case_version_id: 'case-version-2', case_name: '取消收藏', path: '/favorites/cancel', evidence_loaded: false,
      sanitized_result: {},
    }
    const wrapper = mount(ExecutionDetailDrawer, {
      props: { execution: { ...execution, case_results: [{ ...execution.case_results[0], evidence_loaded: false, sanitized_result: {} }, second] } },
    })
    await nextTick()

    expect(wrapper.emitted('loadEvidence')?.[0]?.[0]).toMatchObject({ execution_case_id: 'execution-case-1' })
    await wrapper.findAll('.case-result-list button')[1].trigger('click')
    expect(wrapper.emitted('loadEvidence')?.at(-1)?.[0]).toMatchObject({ execution_case_id: 'execution-case-2' })
  })

  it('reloads the opened case when it becomes terminal without evidence', async () => {
    const running: ExecutionView = {
      ...execution,
      state: 'RUNNING',
      case_statuses: ['RUNNING'],
      case_results: [{
        ...execution.case_results[0], status: 'RUNNING', sanitized_result: {}, evidence_loaded: false,
      }],
    }
    const wrapper = mount(ExecutionDetailDrawer, { props: { execution: running } })
    await nextTick()
    expect(wrapper.emitted('loadEvidence')).toHaveLength(1)

    await wrapper.setProps({ execution: {
      ...running,
      state: 'DONE',
      case_statuses: ['PASSED'],
      case_results: [{ ...running.case_results[0], status: 'PASSED', evidence_loaded: false }],
    } })

    expect(wrapper.emitted('loadEvidence')).toHaveLength(2)
  })
})
