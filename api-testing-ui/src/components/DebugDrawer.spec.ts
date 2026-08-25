// @vitest-environment jsdom

import { mount } from '@vue/test-utils'
import { afterEach, describe, expect, it } from 'vitest'
import { nextTick } from 'vue'

import DebugDrawer from './DebugDrawer.vue'

describe('DebugDrawer', () => {
  afterEach(() => { document.body.innerHTML = '' })
  it('debugs a draft without requiring baseline adoption', async () => {
    const wrapper = mount(DebugDrawer, {
      props: {
        caseVersionId: 'draft-1', environmentRevisionId: 'environment-1',
        environmentLabel: '生产环境（新）-腾讯云 · v6',
      },
    })

    expect(wrapper.text()).toContain('生产环境（新）-腾讯云 · v6')
    expect(wrapper.text()).not.toContain('environment-1')

    await wrapper.find('[data-testid="debug-send"]').trigger('click')

    expect(wrapper.emitted('submit')?.[0]?.[0]).toMatchObject({
      caseVersionIds: ['draft-1'],
      environmentRevisionId: 'environment-1',
    })
    expect(wrapper.find('[data-testid="adopt-baseline"]').exists()).toBe(false)
  })

  it('only offers baseline adoption for a passing debug result', () => {
    const passed = mount(DebugDrawer, {
      props: {
        caseVersionId: 'draft-1', environmentRevisionId: 'environment-1',
        result: { status: 'PASSED', executionCaseId: 'execution-case-1', durationMs: 10, errorMessage: '', trace: [], resolvedRequest: {}, sanitizedResponse: {}, assertions: [], failureCategory: '', logs: [] },
      },
    })
    const failed = mount(DebugDrawer, {
      props: {
        caseVersionId: 'draft-1', environmentRevisionId: 'environment-1',
        result: { status: 'FAILED', executionCaseId: 'execution-case-2', durationMs: 10, errorMessage: '', trace: [], resolvedRequest: {}, sanitizedResponse: {}, assertions: [], failureCategory: 'assertion', logs: [] },
      },
    })

    expect(passed.find('[data-testid="adopt-baseline"]').exists()).toBe(true)
    expect(failed.find('[data-testid="adopt-baseline"]').exists()).toBe(false)
  })

  it('shows baseline adoption progress and completion', async () => {
    const wrapper = mount(DebugDrawer, {
      props: {
        caseVersionId: 'draft-1', environmentRevisionId: 'environment-1', baselineAdopting: true,
        result: { status: 'PASSED', executionCaseId: 'execution-case-1', durationMs: 10, errorMessage: '', trace: [], resolvedRequest: {}, sanitizedResponse: {}, assertions: [], failureCategory: '', logs: [] },
      },
    })

    expect(wrapper.get('[data-testid="adopt-baseline"]').text()).toContain('采纳中')
    expect(wrapper.get('[data-testid="adopt-baseline"]').attributes('disabled')).toBeDefined()

    await wrapper.setProps({ baselineAdopting: false, baselineMessage: '已采纳为基线' })
    expect(wrapper.get('[data-testid="baseline-success"]').text()).toContain('后续可直接加入回归执行')
    expect(wrapper.get('[data-testid="adopt-baseline"]').attributes('disabled')).toBeDefined()
  })

  it('offers progress recovery without submitting a duplicate execution', async () => {
    const wrapper = mount(DebugDrawer, {
      props: { caseVersionId: 'draft-1', environmentRevisionId: 'environment-1', canResume: true, error: '进度读取超时' },
    })

    await wrapper.get('[data-testid="debug-resume"]').trigger('click')

    expect(wrapper.emitted('resume')).toHaveLength(1)
    expect(wrapper.emitted('submit')).toBeUndefined()
  })

  it('renders the structured workflow trace before raw request evidence', async () => {
    const wrapper = mount(DebugDrawer, {
      props: {
        caseVersionId: 'draft-1', environmentRevisionId: 'environment-1',
        result: {
          status: 'FAILED', executionCaseId: 'execution-case-1', durationMs: 321, errorMessage: '清理失败',
          resolvedRequest: {}, sanitizedResponse: {}, assertions: [], failureCategory: 'cleanup', logs: [],
          trace: [{ stage: 'cleanup', index: 0, name: '取消打印', status: 'FAILED', failureCategory: 'cleanup', assertions: [], extractedVariableNames: [], missingVariableNames: [], request: {}, response: {}, error: '取消打印失败', attempt: 1, maxAttempts: 1 }],
        },
      },
    })

    expect(wrapper.get('[data-testid="debug-trace"]').text()).toContain('取消打印')
    expect(wrapper.find('[data-testid="debug-trace"]').element.compareDocumentPosition(wrapper.get('.debug-raw-evidence').element) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
    await wrapper.get('[data-testid="edit-debug-step-cleanup-0"]').trigger('click')
    expect(wrapper.emitted('edit-step')?.[0]?.[0]).toEqual({ stage: 'cleanup', index: 0 })
  })

  it('keeps tab focus inside the modal drawer and returns focus on close', async () => {
    const opener = document.createElement('button')
    opener.textContent = '打开'
    document.body.appendChild(opener)
    opener.focus()
    const wrapper = mount(DebugDrawer, {
      attachTo: document.body,
      props: { caseVersionId: 'draft-1', environmentRevisionId: 'environment-1' },
    })
    await nextTick()
    await nextTick()
    const drawer = wrapper.get('[role="dialog"]')
    const buttons = drawer.findAll('button')
    buttons.at(-1)!.element.focus()
    await drawer.trigger('keydown', { key: 'Tab' })
    expect(document.activeElement).toBe(buttons[0].element)

    await drawer.trigger('keydown', { key: 'Escape' })
    await wrapper.setProps({ open: false })
    wrapper.unmount()
    expect(document.activeElement).toBe(opener)
  })
})
