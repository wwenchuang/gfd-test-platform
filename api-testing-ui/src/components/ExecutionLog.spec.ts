// @vitest-environment jsdom

import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'

import ExecutionLog from './ExecutionLog.vue'
import type { ExecutionEventView } from '../api/contracts'

const EVENTS: ExecutionEventView[] = [
  { id: 1, type: 'execution_started', level: 'info', caseId: '', createdAt: '2026-08-12T07:09:38Z', message: '开始执行', payload: {} },
  { id: 2, type: 'case_finished', level: 'error', caseId: 'case-a', createdAt: '2026-08-12T07:09:39Z', message: '断言失败', payload: { status: 'FAILED' } },
]

describe('ExecutionLog', () => {
  it('renders a bounded latest window for a large history and lets the user move through earlier events', async () => {
    const events = Array.from({ length: 650 }, (_, index) => ({
      id: index + 1,
      type: 'request',
      level: 'info' as const,
      caseId: `case-${Math.floor(index / 5)}`,
      message: `日志 ${index + 1}`,
      payload: {},
    }))
    const wrapper = mount(ExecutionLog, { props: { events, connectionState: 'complete' } })

    expect(wrapper.findAll('[data-testid="log-line"]')).toHaveLength(200)
    expect(wrapper.text()).toContain('第 451-650 条，共 650 条')
    expect(wrapper.text()).toContain('日志 650')
    expect(wrapper.text()).not.toContain('日志 1')

    await wrapper.get('[data-testid="log-window-older"]').trigger('click')

    expect(wrapper.findAll('[data-testid="log-line"]')).toHaveLength(200)
    expect(wrapper.text()).toContain('第 251-450 条，共 650 条')
    expect(wrapper.text()).toContain('日志 251')
    expect(wrapper.text()).not.toContain('日志 650')
  })

  it('redacts sensitive keys in expanded event evidence', async () => {
    const wrapper = mount(ExecutionLog, {
      props: {
        events: [{ id: 1, type: 'request', level: 'info', caseId: 'case-1', message: '发送请求', payload: { headers: { Authorization: 'Bearer event-secret' } } }],
      },
    })

    await wrapper.get('[data-testid="log-evidence-toggle"]').trigger('click')

    expect(wrapper.text()).not.toContain('event-secret')
    expect(wrapper.text()).toContain('已隐藏')
  })

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
    const wrapper = mount(ExecutionLog, {
      props: { events: EVENTS, connectionState: 'open', caseLabels: { 'case-a': '查询我的收藏' } },
    })

    await wrapper.get('[data-testid="log-level"]').setValue('error')

    expect(wrapper.findAll('[data-testid="log-line"]')).toHaveLength(1)
    expect(wrapper.text()).toContain('断言失败')
    expect(wrapper.text()).toContain('查询我的收藏')
    expect(wrapper.text()).toContain('15:09:39')
  })

  it('pauses following when the reader scrolls away and counts unseen logs', async () => {
    const wrapper = mount(ExecutionLog, { props: { events: EVENTS, connectionState: 'open' } })
    const output = wrapper.get('[data-testid="log-output"]')
    Object.defineProperties(output.element, {
      scrollHeight: { configurable: true, value: 600 },
      clientHeight: { configurable: true, value: 200 },
      scrollTop: { configurable: true, writable: true, value: 120 },
    })

    await output.trigger('scroll')
    await wrapper.setProps({
      events: [...EVENTS, { id: 3, type: 'request', level: 'info', caseId: 'case-a', message: '发送请求', payload: {} }],
    })

    expect(wrapper.text()).toContain('1 条新日志')
    await wrapper.get('[data-testid="log-follow"]').trigger('click')
    expect(wrapper.text()).not.toContain('1 条新日志')
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

  it('translates raw execution status words in user-facing log messages', () => {
    const wrapper = mount(ExecutionLog, {
      props: {
        events: [
          { id: 1, type: 'case_finished', level: 'success', caseId: 'case-1', message: 'PASSED', payload: {} },
          { id: 2, type: 'execution_finished', level: 'success', caseId: '', message: 'Execution DONE', payload: {} },
        ],
      },
    })

    expect(wrapper.text()).toContain('通过')
    expect(wrapper.text()).toContain('执行 完成')
    expect(wrapper.text()).not.toContain('PASSED')
    expect(wrapper.text()).not.toContain('DONE')
  })
})
