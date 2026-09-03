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
})
