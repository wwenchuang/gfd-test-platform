// @vitest-environment jsdom

import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'

import DebugDrawer from './DebugDrawer.vue'

describe('DebugDrawer', () => {
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
})
