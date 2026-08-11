// @vitest-environment jsdom

import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'

import ReportSummary from './ReportSummary.vue'

describe('ReportSummary', () => {
  it('keeps product failures, infrastructure breakage and cancellation distinct', () => {
    const wrapper = mount(ReportSummary, {
      props: {
        summary: { total: 5, passed: 1, failed: 1, broken: 1, cancelled: 1, skipped: 1 },
        durationMs: 1280,
        environmentName: '生产环境（腾讯云）',
      },
    })

    expect(wrapper.get('[data-status="PASSED"]').text()).toContain('1')
    expect(wrapper.get('[data-status="FAILED"]').text()).toContain('1')
    expect(wrapper.get('[data-status="BROKEN"]').text()).toContain('1')
    expect(wrapper.get('[data-status="CANCELLED"]').text()).toContain('1')
    expect(wrapper.get('[data-status="SKIPPED"]').text()).toContain('1')
    expect(wrapper.get('[data-testid="passed-count"]').text()).toBe('1')
    expect(wrapper.get('[data-testid="failed-count"]').text()).toBe('1')
    expect(wrapper.get('[data-testid="broken-count"]').text()).toBe('1')
    expect(wrapper.text()).toContain('生产环境（腾讯云）')
    expect(wrapper.text()).toContain('1.28 秒')
  })
})
