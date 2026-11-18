// @vitest-environment jsdom
import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { apiClient } from '../api/client'
import FailureAnalysisPanel from './FailureAnalysisPanel.vue'
vi.mock('../api/client', () => ({ apiClient: { get: vi.fn(), post: vi.fn() } }))
const saved = (runId = 'run-1', status = 'completed') => ({ ok: true, task: {
  id: 'analysis-1', source: 'api', run_id: runId, status,
  result: { summary: '请求返回 504', facts: [{ text: '网关返回超时', evidence_ids: ['e1'] }], hypotheses: [], recommendations: [], missing_evidence: ['缺少网关日志'] },
  evidence: [{ id: 'e1', title: '本次回执', data: { status: 504 } }],
  trace: [{ round: 1, tool: 'get_request_trace', status: 'completed', summary: '读取实际回执', arguments: {} }],
} })
const reply = (value: unknown) => value as Awaited<ReturnType<typeof apiClient.get>>
let wrapper: VueWrapper | undefined
beforeEach(() => {
  vi.resetAllMocks(); sessionStorage.setItem('sessionToken', 'session-a')
  Object.defineProperty(document, 'hidden', { configurable: true, value: false })
  vi.mocked(apiClient.get).mockResolvedValue(reply(saved()))
})
afterEach(() => { wrapper?.unmount(); wrapper = undefined; vi.useRealTimers(); document.body.innerHTML = ''; sessionStorage.clear() })
describe('FailureAnalysisPanel', () => {
  it('restores API report findings through existing authenticated client and starts only on click', async () => {
    wrapper = mount(FailureAnalysisPanel, { attachTo: document.body, props: { runId: 'run-1' } }); await flushPromises()
    expect(apiClient.get).toHaveBeenCalledWith('/api/failure-analysis/tasks?source=api&run_id=run-1', expect.objectContaining({ signal: expect.any(AbortSignal) }))
    expect(apiClient.post).not.toHaveBeenCalled(); expect(wrapper.text()).toContain('请求返回 504')
    vi.mocked(apiClient.post).mockResolvedValue(reply(saved('run-1', 'queued')))
    await wrapper.get('[data-analysis-start]').trigger('click'); await flushPromises()
    expect(apiClient.post).toHaveBeenCalledWith('/api/failure-analysis/tasks', expect.objectContaining({ source: 'api', run_id: 'run-1', request_id: expect.stringMatching(/^[0-9a-f-]{36}$/) }), expect.any(Object))
  })
  it('polls active investigation then stops on unmount', async () => {
    vi.useFakeTimers(); vi.mocked(apiClient.get).mockResolvedValue(reply(saved('run-1', 'running')))
    wrapper = mount(FailureAnalysisPanel, { attachTo: document.body, props: { runId: 'run-1' } }); await flushPromises()
    await vi.advanceTimersByTimeAsync(2000); expect(apiClient.get).toHaveBeenLastCalledWith('/api/failure-analysis/tasks?id=analysis-1', expect.any(Object))
    const count = vi.mocked(apiClient.get).mock.calls.length
    wrapper.unmount(); wrapper = undefined; await vi.advanceTimersByTimeAsync(10000); expect(apiClient.get).toHaveBeenCalledTimes(count)
  })
  it('changing executions aborts old reads and ignores the delayed previous report', async () => {
    let finish!: (value: ReturnType<typeof reply>) => void
    vi.mocked(apiClient.get).mockImplementationOnce(() => new Promise(resolve => { finish = resolve }))
    wrapper = mount(FailureAnalysisPanel, { attachTo: document.body, props: { runId: 'run-1' } }); await flushPromises()
    const oldSignal = vi.mocked(apiClient.get).mock.calls[0][1]?.signal
    vi.mocked(apiClient.get).mockResolvedValue(reply({ ok: true, task: null }))
    await wrapper.setProps({ runId: 'run-2' }); await flushPromises()
    expect(oldSignal?.aborted).toBe(true); finish(reply(saved('run-1'))); await flushPromises()
    expect(wrapper.text()).not.toContain('请求返回 504'); expect(apiClient.get).toHaveBeenLastCalledWith('/api/failure-analysis/tasks?source=api&run_id=run-2', expect.any(Object))
  })
  it('an access error clears private results but leaves the report component mounted', async () => {
    wrapper = mount(FailureAnalysisPanel, { attachTo: document.body, props: { runId: 'run-1' } }); await flushPromises()
    vi.mocked(apiClient.get).mockRejectedValue(Object.assign(Error('denied'), { status: 403 }))
    await wrapper.get('[data-analysis-refresh]').trigger('click'); await flushPromises()
    expect(wrapper.text()).not.toContain('请求返回 504'); expect(wrapper.text()).toContain('当前无法访问')
  })
})

it('does not issue a read from a report without a current session', async () => {
  sessionStorage.clear()
  wrapper = mount(FailureAnalysisPanel, { attachTo: document.body, props: { runId: 'run-1' } }); await flushPromises()
  expect(apiClient.get).not.toHaveBeenCalled()
  expect(wrapper.text()).toContain('登录')
})
