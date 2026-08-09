// @vitest-environment jsdom

import { mount } from '@vue/test-utils'
import { afterEach, describe, expect, it } from 'vitest'
import { nextTick } from 'vue'

import DebugDrawer from './DebugDrawer.vue'

describe('DebugDrawer', () => {
  afterEach(() => { document.body.innerHTML = '' })
  it('debugs a draft without requiring baseline adoption', async () => {
    const wrapper = mount(DebugDrawer, {
      props: { caseVersionId: 'draft-1', environmentRevisionId: 'environment-1' },
    })

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
        result: { status: 'PASSED', executionCaseId: 'execution-case-1', resolvedRequest: {}, sanitizedResponse: {}, assertions: [], failureCategory: '', logs: [] },
      },
    })
    const failed = mount(DebugDrawer, {
      props: {
        caseVersionId: 'draft-1', environmentRevisionId: 'environment-1',
        result: { status: 'FAILED', executionCaseId: 'execution-case-2', resolvedRequest: {}, sanitizedResponse: {}, assertions: [], failureCategory: 'assertion', logs: [] },
      },
    })

    expect(passed.find('[data-testid="adopt-baseline"]').exists()).toBe(true)
    expect(failed.find('[data-testid="adopt-baseline"]').exists()).toBe(false)
  })

  it('offers progress recovery without submitting a duplicate execution', async () => {
    const wrapper = mount(DebugDrawer, {
      props: { caseVersionId: 'draft-1', environmentRevisionId: 'environment-1', canResume: true, error: '进度读取超时' },
    })

    await wrapper.get('[data-testid="debug-resume"]').trigger('click')

    expect(wrapper.emitted('resume')).toHaveLength(1)
    expect(wrapper.emitted('submit')).toBeUndefined()
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
