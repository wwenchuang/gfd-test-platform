// @vitest-environment jsdom
import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'
import LoadFailureSamples from './LoadFailureSamples.vue'

describe('LoadFailureSamples', () => {
  it('distinguishes absent evidence, no response and a service error', () => {
    const wrapper = mount(LoadFailureSamples, { props: { steps: [{ id: 'ok', name: '正常响应' }], samples: [
      { step_id: 'ok', kind: 'http_error', status_code: null },
      { step_id: 'ok', kind: 'http_error', status_code: 0, error_code: 1211, observed_at: '2026-09-15T06:08:09Z' },
      { step_id: 'ok', kind: 'http_error', status_code: 500 },
    ] } })
    const rows = wrapper.findAll('tbody tr')
    expect(rows[0].text()).toContain('未记录')
    expect(rows[0].text()).not.toContain('未收到 HTTP 响应')
    expect(rows[1].text()).toContain('未收到 HTTP 响应')
    expect(rows[1].text()).toContain('1211')
    expect(rows[2].text()).toContain('HTTP 500')
    expect(wrapper.text()).toContain('正常响应')
  })
})
