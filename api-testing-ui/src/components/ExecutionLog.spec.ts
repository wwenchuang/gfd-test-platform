// @vitest-environment jsdom

import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'

import ExecutionLog from './ExecutionLog.vue'
import type { ExecutionEventView } from '../api/contracts'

const EVENTS: ExecutionEventView[] = [
  { id: 1, type: 'execution_started', level: 'info', caseId: '', message: '开始执行', payload: {} },
  { id: 2, type: 'case_finished', level: 'error', caseId: 'case-a', message: '断言失败', payload: { status: 'FAILED' } },
]

describe('ExecutionLog', () => {
  it('keeps existing lines while paused and exposes reconnect state', async () => {
    const wrapper = mount(ExecutionLog, {
      props: { events: EVENTS, connectionState: 'reconnecting' },
    })

    expect(wrapper.text()).toContain('开始执行')
    expect(wrapper.text()).toContain('断言失败')
    expect(wrapper.text()).toContain('正在重连')

    await wrapper.get('[data-testid="log-follow"]').trigger('click')
    await wrapper.setProps({
      events: [...EVENTS, { id: 3, type: 'request', level: 'info', caseId: 'case-a', message: '发送请求', payload: {} }],
    })

    expect(wrapper.text()).toContain('滚动已暂停')
    expect(wrapper.findAll('[data-testid="log-line"]')).toHaveLength(3)
  })

  it('filters by level and case without mutating the event history', async () => {
    const wrapper = mount(ExecutionLog, { props: { events: EVENTS, connectionState: 'open' } })

    await wrapper.get('[data-testid="log-level"]').setValue('error')

    expect(wrapper.findAll('[data-testid="log-line"]')).toHaveLength(1)
    expect(wrapper.text()).toContain('断言失败')
  })

  it('reveals sanitized event evidence without replacing the log line', async () => {
    const wrapper = mount(ExecutionLog, {
      props: {
        events: [{ ...EVENTS[0], payload: { response: { status_code: 403, body: { code: 1001 } } } }],
        connectionState: 'open',
      },
    })

    await wrapper.get('[data-testid="log-evidence-toggle"]').trigger('click')

    expect(wrapper.get('[data-testid="log-evidence"]').text()).toContain('status_code')
    expect(wrapper.findAll('[data-testid="log-line"]')).toHaveLength(1)
  })
})
