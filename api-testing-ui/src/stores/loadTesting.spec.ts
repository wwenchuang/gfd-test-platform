// @vitest-environment jsdom
import { createPinia, setActivePinia } from 'pinia'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { apiClient } from '../api/client'
import { useLoadTestingStore } from './loadTesting'

class FakeEventSource {
  static opened: FakeEventSource[] = []
  onopen: (() => void) | null = null
  onerror: (() => void) | null = null
  listeners: Record<string, (event: MessageEvent) => void> = {}
  closed = false
  constructor(public url: string) { FakeEventSource.opened.push(this) }
  addEventListener(name: string, callback: EventListenerOrEventListenerObject): void { this.listeners[name] = callback as (event: MessageEvent) => void }
  close(): void { this.closed = true }
}

describe('loadTesting live events', () => {
  beforeEach(() => { setActivePinia(createPinia()); FakeEventSource.opened = []; vi.stubGlobal('EventSource', FakeEventSource); sessionStorage.setItem('sessionToken', 'must-not-leak') })
  afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals(); sessionStorage.clear(); vi.useRealTimers() })

  it('uses an opaque ticket, resumes by sequence and falls back to bounded polling', async () => {
    vi.spyOn(apiClient, 'post').mockResolvedValue({ data: { ticket: 'opaque-ticket' } } as never)
    const events = vi.spyOn(apiClient, 'get').mockResolvedValue({ data: { events: [], terminal: true } } as never)
    const store = useLoadTestingStore()
    await store.connectRunEvents('run-1')
    expect(FakeEventSource.opened[0].url).toBe('/api/api-testing/v1/load-runs/run-1/events?ticket=opaque-ticket')
    expect(FakeEventSource.opened[0].url).not.toContain('must-not-leak')
    FakeEventSource.opened[0].listeners.load_event(new MessageEvent('load_event', { data: JSON.stringify({ type: 'agent.progress', payload: { message: '10%' } }), lastEventId: '7' }))
    expect(store.runEvents[0]).toMatchObject({ id: 7, type: 'agent.progress' })
    vi.useFakeTimers()
    FakeEventSource.opened[0].onerror?.()
    expect(store.runConnectionState).toBe('polling')
    await vi.advanceTimersByTimeAsync(3000)
    expect(events).toHaveBeenCalledWith('/api/api-testing/v1/load-runs/run-1/events?after=7')
    store.disconnectRunEvents()
  })

  it('recovers a silent open stream with durable polling after 10 seconds', async () => {
    vi.useFakeTimers()
    vi.spyOn(apiClient, 'post').mockResolvedValue({ data: { ticket: 'ticket' } } as never)
    vi.spyOn(apiClient, 'get').mockImplementation(async path => ({ data: path.includes('/events?')
      ? { events: [{ id: 1, type: 'run.running', payload: {} }], terminal: false }
      : { run: { id: 'r1', state: 'running' } } }) as never)
    const store = useLoadTestingStore()
    await store.connectRunEvents('r1')
    const source = FakeEventSource.opened[0]
    source.onopen?.()
    await vi.advanceTimersByTimeAsync(13000)
    expect(source.closed).toBe(true)
    expect(store.runConnectionState).toBe('polling')
    expect(store.runEvents[0]?.type).toBe('run.running')
    store.disconnectRunEvents()
    expect(vi.getTimerCount()).toBe(0)
  })

  it('ignores an old ticket that resolves after switching reports', async () => {
    let resolveOld!: (value: never) => void
    vi.spyOn(apiClient, 'post').mockImplementationOnce(() => new Promise(resolve => { resolveOld = resolve }))
      .mockResolvedValueOnce({ data: { ticket: 'new-ticket' } } as never)
    const store = useLoadTestingStore()
    const old = store.connectRunEvents('old')
    await store.connectRunEvents('new')
    resolveOld({ data: { ticket: 'old-ticket' } } as never)
    await old
    expect(FakeEventSource.opened).toHaveLength(1)
    expect(FakeEventSource.opened[0].url).toContain('/new/events')
    store.disconnectRunEvents()
  })

  it('drains all terminal event pages before completing', async () => {
    const get = vi.spyOn(apiClient, 'get')
      .mockResolvedValueOnce({ data: {events: [{id:'opaque-event-uuid-200',sequence:200,type:'progress',payload:{}}],terminal:true,has_more:true} } as never)
      .mockResolvedValueOnce({ data: {events: [{id:'opaque-event-uuid-201',sequence:201,type:'run.cancelled',payload:{}}],terminal:true,has_more:false} } as never)
    const store = useLoadTestingStore(); store.runConnectionState = 'polling'
    await store.loadRunEvents('r1')
    expect(store.runConnectionState).toBe('polling')
    await store.loadRunEvents('r1')
    expect(get).toHaveBeenLastCalledWith('/api/api-testing/v1/load-runs/r1/events?after=200')
    expect(store.runConnectionState).toBe('complete')
    expect(store.runEvents.at(-1)?.type).toBe('run.cancelled')
  })

  it('does not start overlapping polls when a timed out ticket rejects late', async () => {
    vi.useFakeTimers()
    let rejectTicket!: (reason: Error) => void
    vi.spyOn(apiClient, 'post').mockImplementation(() => new Promise((_, reject) => { rejectTicket = reject }))
    const get = vi.spyOn(apiClient, 'get').mockImplementation(() => new Promise(() => {}))
    const store = useLoadTestingStore()
    const connection = store.connectRunEvents('r1')
    await vi.advanceTimersByTimeAsync(13000)
    expect(get).toHaveBeenCalledTimes(2)
    rejectTicket(new Error('timeout')); await connection
    await vi.advanceTimersByTimeAsync(5000)
    expect(get).toHaveBeenCalledTimes(2)
    store.disconnectRunEvents()
  })

  it('ignores old terminal JSON events returned after switching runs', async () => {
    let resolveOld!: (value: never) => void
    vi.spyOn(apiClient, 'get').mockImplementationOnce(() => new Promise(resolve => {resolveOld = resolve}))
    vi.spyOn(apiClient, 'post').mockResolvedValue({data:{ticket:'new-ticket'}} as never)
    const store = useLoadTestingStore()
    const old = store.loadRunEvents('old')
    await store.connectRunEvents('new')
    resolveOld({data:{events:[{id:'old-uuid',sequence:300,type:'run.cancelled',payload:{}}],terminal:true}} as never)
    await old
    expect(store.runEvents).toEqual([])
    expect(store.runConnectionState).toBe('connecting')
    store.disconnectRunEvents()
  })

  it('shows the server reason when creating a load draft fails', async () => {
    vi.spyOn(apiClient, 'post').mockRejectedValue(new Error('压测节点没有剩余容量'))
    const store = useLoadTestingStore()
    await expect(store.createRun({})).rejects.toThrow('压测节点没有剩余容量')
    expect(store.runError).toBe('压测节点没有剩余容量')
  })

  it('keeps action failures visible and removes a deleted terminal run', async () => {
    const post = vi.spyOn(apiClient, 'post').mockRejectedValueOnce(new Error('单用户预检业务断言失败'))
    const remove = vi.spyOn(apiClient, 'delete').mockResolvedValue({ data: { deleted: true } } as never)
    const store = useLoadTestingStore()
    store.runs = [{ id: 'run-1' } as never]
    await expect(store.preflightRun('run-1')).rejects.toThrow('单用户预检业务断言失败')
    expect(store.runError).toBe('单用户预检业务断言失败')
    expect(post).toHaveBeenCalledWith('/api/api-testing/v1/load-runs/run-1/preflight', {})
    await store.deleteRun('run-1')
    expect(remove).toHaveBeenCalledWith('/api/api-testing/v1/load-runs/run-1')
    expect(store.runs).toEqual([])
  })
})
