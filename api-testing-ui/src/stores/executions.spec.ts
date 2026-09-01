// @vitest-environment jsdom

import { beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import { flushPromises } from '@vue/test-utils'

import { apiClient } from '../api/client'
import type { ApiEnvelope, ExecutionView } from '../api/contracts'
import { useExecutionsStore } from './executions'

describe('executions store', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    vi.restoreAllMocks()
  })

  it('clears stale execution detail before loading a deep-linked execution', () => {
    const store = useExecutionsStore()
    store.active = {
      id: 'execution-old', project_id: 'project-1', state: 'DONE', execution_type: 'debug',
      source_revision_id: 'source-1', environment_revision_id: 'environment-1', environment_name: '旧环境',
      case_statuses: ['PASSED'], case_results: [], summary: { total: 1, passed: 1 },
      cancellation_requested: false, created_at: '', started_at: '', finished_at: '',
    }
    store.events = [{ id: 1, type: 'response', level: 'info', caseId: 'case-1', message: '旧响应', payload: {} }]

    store.prepareSelection('execution-new')

    expect(store.active).toBeNull()
    expect(store.events).toEqual([])
    expect(store.selectingExecutionId).toBe('execution-new')
  })

  it('loads a lightweight execution first and merges one selected case evidence', async () => {
    const summary = {
      id: 'execution-1', project_id: 'project-1', state: 'DONE', execution_type: 'regression',
      source_revision_id: 'source-1', environment_revision_id: 'environment-1', environment_name: '测试环境',
      case_statuses: ['PASSED'], summary: { total: 1, passed: 1 }, cancellation_requested: false,
      created_at: '', started_at: '', finished_at: '',
      case_results: [{
        execution_case_id: 'case-1', case_version_id: 'version-1', endpoint_id: 'endpoint-1',
        case_name: '查询收藏', endpoint_summary: '', method: 'GET', path: '/favorites', status: 'PASSED',
        failure_category: '', duration_ms: 20, sanitized_result: {}, evidence_loaded: false,
      }],
    } as ExecutionView
    const evidence = {
      ...summary.case_results[0],
      evidence_loaded: true,
      sanitized_result: { sanitized_response: { status_code: 200 }, assertion_results: [{ passed: true }] },
    }
    const get = vi.spyOn(apiClient, 'get')
      .mockResolvedValueOnce({ data: { execution: summary } })
      .mockResolvedValueOnce({ data: { case_result: evidence } })
    const store = useExecutionsStore()
    vi.spyOn(store, 'connect').mockResolvedValue()

    await store.select(summary.id)
    const loaded = await store.loadExecutionCase(summary.id, 'case-1')

    expect(get.mock.calls[0]?.[0]).toBe('/api/api-testing/v1/executions/execution-1')
    expect(get.mock.calls[1]?.[0]).toBe('/api/api-testing/v1/executions/execution-1/cases/case-1')
    expect(loaded.sanitized_result).toMatchObject({ sanitized_response: { status_code: 200 } })
    expect(store.active?.case_results[0].evidence_loaded).toBe(true)
    expect(store.loadingCaseKeys).toEqual([])
  })

  it('loads evidence for a legacy lightweight case whose evidence flag is missing', async () => {
    const summary = {
      id: 'execution-legacy', project_id: 'project-1', state: 'DONE', execution_type: 'regression',
      source_revision_id: 'source-1', environment_revision_id: 'environment-1', environment_name: '测试环境',
      case_statuses: ['PASSED'], summary: { total: 1, passed: 1 }, cancellation_requested: false,
      created_at: '', started_at: '', finished_at: '',
      case_results: [{
        execution_case_id: 'case-legacy', case_version_id: 'version-legacy', endpoint_id: 'endpoint-legacy',
        case_name: '旧摘要', endpoint_summary: '', method: 'GET', path: '/legacy', status: 'PASSED',
        failure_category: '', duration_ms: 20, sanitized_result: {},
      }],
    } as ExecutionView
    const loadedResult = {
      ...summary.case_results[0],
      evidence_loaded: true,
      sanitized_result: { sanitized_response: { status_code: 200, body: { code: 0 } } },
    }
    const get = vi.spyOn(apiClient, 'get').mockResolvedValue({ data: { case_result: loadedResult } })
    const store = useExecutionsStore()
    store.active = summary

    await store.loadExecutionCase(summary.id, 'case-legacy')

    expect(get).toHaveBeenCalledOnce()
    expect(store.active.case_results[0].evidence_loaded).toBe(true)
  })

  it('keeps fresh terminal status when a summary refresh preserves loaded evidence', async () => {
    const loaded = {
      id: 'execution-1', state: 'RUNNING',
      case_results: [{
        execution_case_id: 'case-1', status: 'RUNNING', duration_ms: 0,
        sanitized_result: { trace: [{ phase: 'request' }] }, evidence_loaded: true,
      }],
    } as unknown as ExecutionView
    const finished = {
      ...loaded,
      state: 'DONE',
      case_results: [{
        ...loaded.case_results[0], status: 'PASSED', duration_ms: 42,
        sanitized_result: {}, evidence_loaded: false,
      }],
    }
    vi.spyOn(apiClient, 'get').mockResolvedValue({ data: { execution: finished } })
    const store = useExecutionsStore()
    store.active = loaded
    store.executions = [loaded]

    const refreshed = await store.loadExecution('execution-1')

    expect(refreshed.case_results[0]).toMatchObject({
      status: 'PASSED', duration_ms: 42, evidence_loaded: true,
      sanitized_result: { trace: [{ phase: 'request' }] },
    })
  })

  it('keeps visible case rows when a terminal snapshot is transiently empty', async () => {
    const running = {
      id: 'execution-1', state: 'RUNNING', summary: { total: 2 },
      case_results: [
        { execution_case_id: 'case-1', status: 'PASSED', sanitized_result: {}, evidence_loaded: false },
        { execution_case_id: 'case-2', status: 'PASSED', sanitized_result: {}, evidence_loaded: false },
      ],
    } as unknown as ExecutionView
    const transientTerminal = {
      ...running,
      state: 'DONE',
      summary: { total: 2, passed: 2 },
      case_results: [],
    }
    vi.spyOn(apiClient, 'get').mockResolvedValue({ data: { execution: transientTerminal } })
    const store = useExecutionsStore()
    store.active = running
    store.executions = [running]

    const refreshed = await store.loadExecution('execution-1')

    expect(refreshed.state).toBe('DONE')
    expect(refreshed.case_results.map(item => item.execution_case_id)).toEqual(['case-1', 'case-2'])
  })

  it('keeps the lightweight case usable and exposes a retryable evidence error', async () => {
    const summary = {
      id: 'execution-1', case_results: [{ execution_case_id: 'case-1', evidence_loaded: false, sanitized_result: {} }],
    } as unknown as ExecutionView
    vi.spyOn(apiClient, 'get').mockRejectedValue(new Error('证据读取超时'))
    const store = useExecutionsStore()
    store.active = summary

    await expect(store.loadExecutionCase('execution-1', 'case-1')).rejects.toThrow('证据读取超时')

    expect(store.active).toStrictEqual(summary)
    expect(store.caseEvidenceErrors['execution-1:case-1']).toBe('证据读取超时')
    expect(store.loadingCaseKeys).toEqual([])
  })

  it('deduplicates monotonic SSE events without replacing existing evidence', () => {
    const store = useExecutionsStore()
    store.appendEvent({ id: 2, type: 'case_finished', level: 'error', caseId: 'case-a', message: '原始失败', payload: {} })
    store.appendEvent({ id: 2, type: 'case_finished', level: 'info', caseId: 'case-a', message: '重复覆盖', payload: {} })
    store.appendEvent({ id: 3, type: 'execution_finished', level: 'info', caseId: '', message: '执行完成', payload: {} })

    expect(store.events.map(item => item.message)).toEqual(['原始失败', '执行完成'])
  })

  it('uses an opaque ticket URL and never places the session token in EventSource', async () => {
    vi.spyOn(apiClient, 'post').mockResolvedValue({ data: { ticket: 'opaque-ticket' } })
    const opened: string[] = []
    class FakeEventSource {
      static OPEN = 1
      readyState = 1
      onopen: null | (() => void) = null
      onerror: null | (() => void) = null
      constructor(url: string) { opened.push(url) }
      addEventListener(): void {}
      close(): void {}
    }
    vi.stubGlobal('EventSource', FakeEventSource)
    sessionStorage.setItem('sessionToken', 'browser-session-secret')
    const store = useExecutionsStore()

    await store.connect('execution-1')

    expect(opened).toEqual(['/api/api-testing/v1/executions/execution-1/events?ticket=opaque-ticket'])
    expect(opened[0]).not.toContain('browser-session-secret')
    vi.unstubAllGlobals()
  })

  it('starts the selected baseline set with one baseline regression command', async () => {
    const execution = {
      id: 'execution-regression', project_id: 'project-1', state: 'QUEUED', execution_type: 'baseline_regression',
      source_revision_id: 'source-revision-1', environment_revision_id: 'environment-revision-1',
      environment_name: '生产环境（新）- 腾讯云', case_statuses: ['QUEUED'], case_results: [], summary: {},
      cancellation_requested: false, created_at: '', started_at: null, finished_at: null,
    } as const
    const post = vi.spyOn(apiClient, 'post').mockResolvedValue({ data: { execution } })
    const store = useExecutionsStore()
    vi.spyOn(store, 'select').mockResolvedValue()

    await store.runBaselines({
      projectId: 'project-1', sourceRevisionId: 'source-revision-1', environmentRevisionId: 'environment-revision-1',
      baselineIds: ['baseline-2', 'baseline-2', 'baseline-3'],
    })

    expect(post).toHaveBeenCalledWith('/api/api-testing/v1/regressions', {
      project_id: 'project-1', source_revision_id: 'source-revision-1',
      environment_revision_id: 'environment-revision-1', idempotency_key: expect.any(String),
      baseline_ids: ['baseline-2', 'baseline-3'],
    })
    expect(store.executions[0]?.id).toBe('execution-regression')
  })

  it('opens the durable event stream when selecting a completed execution', async () => {
    const execution = {
      id: 'execution-done', project_id: 'project-1', state: 'DONE', execution_type: 'regression',
      source_revision_id: 'source-1', environment_revision_id: 'environment-1', environment_name: '生产环境',
      case_statuses: ['PASSED'], case_results: [], summary: { total: 1, passed: 1 },
      cancellation_requested: false, created_at: '', started_at: '', finished_at: '',
    } as const
    vi.spyOn(apiClient, 'get').mockResolvedValue({ data: { execution } })
    const store = useExecutionsStore()
    const connect = vi.spyOn(store, 'connect').mockResolvedValue()

    await store.select('execution-done')

    expect(connect).toHaveBeenCalledWith('execution-done')
  })

  it('renews an expired SSE ticket and resumes after the last durable event', async () => {
    vi.useFakeTimers()
    vi.spyOn(apiClient, 'post')
      .mockResolvedValueOnce({ data: { ticket: 'ticket-one' } })
      .mockResolvedValueOnce({ data: { ticket: 'ticket-two' } })
    const opened: Array<{ url: string; source: FakeEventSource }> = []
    class FakeEventSource {
      static OPEN = 1
      readyState = 1
      onopen: null | (() => void) = null
      onerror: null | (() => void) = null
      constructor(public url: string) { opened.push({ url, source: this }) }
      addEventListener(): void {}
      close(): void {}
    }
    vi.stubGlobal('EventSource', FakeEventSource)
    const store = useExecutionsStore()
    store.active = { id: 'execution-1', state: 'RUNNING' } as never
    store.appendEvent({ id: 7, type: 'response', level: 'info', caseId: 'case-a', message: '收到响应', payload: {} })

    await store.connect('execution-1')
    opened[0].source.onerror?.()
    expect(opened).toHaveLength(1)
    await vi.advanceTimersByTimeAsync(999)
    expect(opened).toHaveLength(1)
    await vi.advanceTimersByTimeAsync(1)

    expect(opened[1].url).toBe('/api/api-testing/v1/executions/execution-1/events?ticket=ticket-two&after=7')
    vi.useRealTimers()
    vi.unstubAllGlobals()
  })

  it('stops reconnecting after the bounded retry budget', async () => {
    vi.useFakeTimers()
    vi.spyOn(apiClient, 'post').mockResolvedValue({ data: { ticket: 'opaque-ticket' } })
    const opened: FakeEventSource[] = []
    class FakeEventSource {
      onopen: null | (() => void) = null
      onerror: null | (() => void) = null
      constructor(_url: string) { opened.push(this) }
      addEventListener(): void {}
      close(): void {}
    }
    vi.stubGlobal('EventSource', FakeEventSource)
    const store = useExecutionsStore()
    store.active = { id: 'execution-1', state: 'RUNNING' } as never

    await store.connect('execution-1')
    for (const delay of [1000, 2000, 5000, 10000, 30000]) {
      opened.at(-1)?.onerror?.()
      await vi.advanceTimersByTimeAsync(delay)
    }
    opened.at(-1)?.onerror?.()

    expect(opened).toHaveLength(6)
    expect(store.connectionState).toBe('failed')
    expect(store.error).toContain('重新连接')
    expect(store.finalSnapshotTimer).not.toBeNull()
    store.disconnect()
    vi.useRealTimers()
    vi.unstubAllGlobals()
  })

  it('keeps polling snapshots after SSE retries are exhausted until execution is terminal', async () => {
    vi.useFakeTimers()
    const running = { id: 'execution-1', state: 'RUNNING', case_results: [] } as unknown as ExecutionView
    const done = { ...running, state: 'DONE' } as ExecutionView
    vi.spyOn(apiClient, 'get').mockResolvedValueOnce({ data: { execution: done } })
    const store = useExecutionsStore()
    store.active = running

    store.scheduleFinalSnapshotPoll('execution-1')
    await vi.advanceTimersByTimeAsync(5000)

    expect(store.active?.state).toBe('DONE')
    expect(store.connectionState).toBe('complete')
    expect(store.finalSnapshotTimer).toBeNull()
    vi.useRealTimers()
  })

  it('polls the durable snapshot while SSE remains open but stops delivering events', async () => {
    vi.useFakeTimers()
    try {
      const running = { id: 'execution-1', state: 'RUNNING', case_results: [] } as unknown as ExecutionView
      const done = { ...running, state: 'DONE' } as ExecutionView
      vi.spyOn(apiClient, 'post').mockResolvedValue({ data: { ticket: 'opaque-ticket' } })
      vi.spyOn(apiClient, 'get').mockResolvedValue({ data: { execution: done } })
      class SilentEventSource {
        onopen: null | (() => void) = null
        onerror: null | (() => void) = null
        addEventListener(): void {}
        close(): void {}
      }
      vi.stubGlobal('EventSource', SilentEventSource)
      const store = useExecutionsStore()
      store.active = running

      await store.connect(running.id)

      expect(store.finalSnapshotTimer).not.toBeNull()
      await vi.advanceTimersByTimeAsync(5000)
      expect(store.active?.state).toBe('DONE')
      expect(store.finalSnapshotTimer).toBeNull()
    } finally {
      vi.useRealTimers()
      vi.unstubAllGlobals()
    }
  })

  it('keeps polling the durable snapshot after the finished event arrives before the summary', async () => {
    vi.useFakeTimers()
    try {
      const running = {
        id: 'execution-1', state: 'RUNNING', summary: { total: 5, passed: 1 },
        case_statuses: ['PASSED', 'RUNNING', 'QUEUED', 'QUEUED', 'QUEUED'], case_results: [],
      } as unknown as ExecutionView
      const done = {
        ...running,
        state: 'DONE', summary: { total: 5, passed: 5 },
        case_statuses: ['PASSED', 'PASSED', 'PASSED', 'PASSED', 'PASSED'],
      } as ExecutionView
      vi.spyOn(apiClient, 'get')
        .mockResolvedValueOnce({ data: { execution: running } })
        .mockResolvedValueOnce({ data: { execution: done } })
      const store = useExecutionsStore()
      store.active = running

      store.consumeEvent(
        'execution_finished',
        { data: '{"state":"DONE"}', lastEventId: '108' } as MessageEvent,
        running.id,
      )

      expect(store.finalSnapshotTimer).not.toBeNull()
      await vi.advanceTimersByTimeAsync(5000)
      expect(store.active?.state).toBe('RUNNING')
      expect(store.finalSnapshotTimer).not.toBeNull()
      await vi.advanceTimersByTimeAsync(5000)
      expect(store.active?.state).toBe('DONE')
      expect(store.active?.summary.passed).toBe(5)
      expect(store.finalSnapshotTimer).toBeNull()
    } finally {
      vi.useRealTimers()
    }
  })

  it('keeps the latest execution when record requests resolve out of order', async () => {
    let resolveFirst: ((value: ApiEnvelope<{ execution: ExecutionView }>) => void) | undefined
    let resolveSecond: ((value: ApiEnvelope<{ execution: ExecutionView }>) => void) | undefined
    vi.spyOn(apiClient, 'get')
      .mockImplementationOnce(() => new Promise(resolve => { resolveFirst = resolve }))
      .mockImplementationOnce(() => new Promise(resolve => { resolveSecond = resolve }))
    const store = useExecutionsStore()
    const connect = vi.spyOn(store, 'connect').mockResolvedValue()
    const first = store.select('execution-a')
    const second = store.select('execution-b')
    resolveSecond?.({ data: { execution: { id: 'execution-b', state: 'DONE' } as ExecutionView } })
    await second
    resolveFirst?.({ data: { execution: { id: 'execution-a', state: 'DONE' } as ExecutionView } })
    await first

    expect(store.active?.id).toBe('execution-b')
    expect(connect).toHaveBeenCalledTimes(1)
    expect(connect).toHaveBeenCalledWith('execution-b')
  })

  it.each(['success', 'failure'])('ignores a late archived snapshot %s after another execution is selected', async outcome => {
    vi.useFakeTimers()
    try {
      const first = { id: 'execution-a', state: 'RUNNING', case_results: [] } as unknown as ExecutionView
      const second = { ...first, id: 'execution-b' }
      let respond: ((value: ApiEnvelope<{ execution: ExecutionView }>) => void) | undefined
      let reject: ((reason: Error) => void) | undefined
      vi.spyOn(apiClient, 'get')
        .mockImplementationOnce(() => new Promise((resolve, fail) => { respond = resolve; reject = fail }))
        .mockResolvedValueOnce({ data: { execution: second } })
      vi.spyOn(apiClient, 'delete').mockResolvedValue({ data: { execution: first } })
      const store = useExecutionsStore()
      store.active = first
      vi.spyOn(store, 'connect').mockImplementation(async () => { store.connectionState = 'open' })
      store.scheduleFinalSnapshotPoll(first.id)
      const pendingTimer = vi.advanceTimersByTimeAsync(5000)
      await vi.waitFor(() => expect(apiClient.get).toHaveBeenCalledTimes(1))
      await store.deleteExecutions([first.id])
      await store.select(second.id)
      if (outcome === 'success') respond?.({ data: { execution: { ...first, state: 'DONE' } } })
      else reject?.(new Error('旧请求失败'))
      await pendingTimer
      await flushPromises()
      expect(store.active?.id).toBe(second.id)
      expect(store.connectionState).toBe('open')
      expect(store.error).toBe('')
      expect(store.finalSnapshotTimer).toBeNull()
    } finally { vi.useRealTimers() }
  })

  it('ignores late events from a previously selected execution', async () => {
    vi.spyOn(apiClient, 'post').mockResolvedValue({ data: { ticket: 'opaque-ticket' } })
    const listeners = new Map<string, (event: MessageEvent) => void>()
    class FakeEventSource {
      onopen: null | (() => void) = null
      onerror: null | (() => void) = null
      addEventListener(type: string, listener: EventListener): void {
        listeners.set(type, listener as (event: MessageEvent) => void)
      }
      close(): void {}
    }
    vi.stubGlobal('EventSource', FakeEventSource)
    const store = useExecutionsStore()
    store.active = { id: 'execution-a', state: 'RUNNING' } as never
    const loadExecution = vi.spyOn(store, 'loadExecution').mockResolvedValue({ id: 'execution-a' } as never)

    await store.connect('execution-a')
    store.disconnect()
    store.active = { id: 'execution-b', state: 'RUNNING' } as never
    listeners.get('execution_finished')?.({ data: '{"state":"DONE"}', lastEventId: '8' } as MessageEvent)

    expect(loadExecution).not.toHaveBeenCalled()
    expect(store.events).toEqual([])
    vi.unstubAllGlobals()
  })

  it('does not open a stale SSE ticket after switching executions', async () => {
    let resolveFirstTicket: ((value: ApiEnvelope<{ ticket: string }>) => void) | undefined
    vi.spyOn(apiClient, 'get').mockImplementation(async path => ({
      data: { execution: { id: path.endsWith('execution-a') ? 'execution-a' : 'execution-b', state: 'RUNNING' } as ExecutionView },
    }))
    vi.spyOn(apiClient, 'post')
      .mockImplementationOnce(() => new Promise(resolve => { resolveFirstTicket = resolve }))
      .mockResolvedValueOnce({ data: { ticket: 'ticket-b' } })
    const opened: string[] = []
    class FakeEventSource {
      onopen: null | (() => void) = null
      onerror: null | (() => void) = null
      constructor(url: string) { opened.push(url) }
      addEventListener(): void {}
      close(): void {}
    }
    vi.stubGlobal('EventSource', FakeEventSource)
    const store = useExecutionsStore()

    const first = store.select('execution-a')
    await vi.waitFor(() => expect(apiClient.post).toHaveBeenCalledTimes(1))
    await store.select('execution-b')
    resolveFirstTicket?.({ data: { ticket: 'ticket-a' } })
    await first

    expect(opened).toEqual(['/api/api-testing/v1/executions/execution-b/events?ticket=ticket-b'])
    expect(store.active?.id).toBe('execution-b')
    vi.unstubAllGlobals()
  })

  it('refreshes a terminal failure until background AI analysis is available', async () => {
    vi.useFakeTimers()
    const pending = {
      id: 'execution-1', state: 'DONE',
      case_results: [{ execution_case_id: 'case-1', status: 'FAILED', failure_analysis: null, evidence_loaded: false, sanitized_result: {} }],
    } as unknown as ExecutionView
    const analyzed = {
      ...pending,
      case_results: [{
        ...pending.case_results[0],
        failure_analysis: { model: 'qwen3.7-plus', analysis: { summary: '业务码异常' } },
      }],
    } as unknown as ExecutionView
    vi.spyOn(apiClient, 'get')
      .mockResolvedValueOnce({ data: { execution: pending } })
      .mockResolvedValueOnce({ data: { execution: analyzed } })
    const store = useExecutionsStore()
    store.active = pending

    await store.refreshPendingAnalysis('execution-1')
    expect(store.active?.case_results[0].failure_analysis).toBeNull()
    await vi.advanceTimersByTimeAsync(500)

    expect(store.active?.case_results[0].failure_analysis?.model).toBe('qwen3.7-plus')
    expect(apiClient.get).toHaveBeenCalledTimes(2)
    vi.useRealTimers()
  })

  it('renders extraction evidence in the live event stream', () => {
    const store = useExecutionsStore()

    store.consumeEvent('extraction', { data: '{"execution_case_id":"case-a","target":"favoriteId"}', lastEventId: '4' } as MessageEvent, 'execution-1')

    expect(store.events[0]).toMatchObject({ id: 4, type: 'extraction', caseId: 'case-a', message: '提取变量' })
  })

  it('keeps the durable event timestamp outside the visible evidence payload', () => {
    const store = useExecutionsStore()

    store.consumeEvent('case_finished', {
      data: '{"execution_case_id":"case-a","status":"PASSED","_event_created_at":"2026-08-12T07:09:38+00:00"}',
      lastEventId: '9',
    } as MessageEvent, 'execution-1')

    expect(store.events[0]).toMatchObject({
      id: 9,
      caseId: 'case-a',
      createdAt: '2026-08-12T07:09:38+00:00',
      payload: { execution_case_id: 'case-a', status: 'PASSED' },
    })
  })

  it('archives one execution and clears the active record locally', async () => {
    const archived = { id: 'execution-1', state: 'ARCHIVED' } as ExecutionView
    const remove = vi.spyOn(apiClient, 'delete').mockResolvedValue({ data: { execution: archived } })
    const store = useExecutionsStore()
    const disconnect = vi.spyOn(store, 'disconnect')
    store.executions = [
      { id: 'execution-1', state: 'DONE' } as ExecutionView,
      { id: 'execution-2', state: 'DONE' } as ExecutionView,
    ]
    store.active = store.executions[0]
    store.events = [{ id: 1, type: 'execution_finished', level: 'info', caseId: '', message: '完成', payload: {} }]

    await store.deleteExecutions(['execution-1'])

    expect(remove).toHaveBeenCalledWith('/api/api-testing/v1/executions/execution-1')
    expect(store.executions.map(item => item.id)).toEqual(['execution-2'])
    expect(store.active).toBeNull()
    expect(store.events).toEqual([])
    expect(disconnect).toHaveBeenCalled()
  })

  it('archives multiple executions in one request and keeps the current record when not selected', async () => {
    const post = vi.spyOn(apiClient, 'post').mockResolvedValue({ data: { executions: [] } })
    const store = useExecutionsStore()
    store.executions = [
      { id: 'execution-1', state: 'DONE' } as ExecutionView,
      { id: 'execution-2', state: 'DONE' } as ExecutionView,
      { id: 'execution-3', state: 'DONE' } as ExecutionView,
    ]
    store.active = store.executions[2]

    await store.deleteExecutions(['execution-1', 'execution-2', 'execution-2'])

    expect(post).toHaveBeenCalledWith('/api/api-testing/v1/executions/archive', {
      execution_ids: ['execution-1', 'execution-2'],
    })
    expect(store.executions.map(item => item.id)).toEqual(['execution-3'])
    expect(store.active?.id).toBe('execution-3')
  })

  it.each(['detail', 'selection', 'list'])('does not restore an archived execution from a late %s response', async kind => {
    const first = { id: 'execution-1', state: 'DONE', case_results: [] } as unknown as ExecutionView
    const second = { ...first, id: 'execution-2' }
    let respond: ((value: ApiEnvelope<{ execution: ExecutionView; executions: ExecutionView[] }>) => void) | undefined
    vi.spyOn(apiClient, 'get').mockImplementationOnce(() => new Promise(resolve => { respond = resolve }))
    vi.spyOn(apiClient, 'delete').mockResolvedValue({ data: { execution: first } })
    const store = useExecutionsStore()
    vi.spyOn(store, 'connect').mockResolvedValue()
    store.executions = [first, second]
    const pending = kind === 'detail' ? store.loadExecution(first.id) : kind === 'selection' ? store.select(first.id) : store.load('project-1')
    await store.deleteExecutions([first.id])
    respond?.({ data: { execution: first, executions: [first, second] } })
    await pending
    expect(store.executions.map(item => item.id)).toEqual([second.id])
    expect(store.active).toBeNull()
    expect(store.connect).not.toHaveBeenCalled()
    expect(store.selectingExecutionId).toBe('')
  })

  it('keeps a pending diagnostic readable when archival fails', async () => {
    const record = { id: 'execution-1', state: 'DONE', case_results: [] } as unknown as ExecutionView
    let respond: ((value: ApiEnvelope<{ execution: ExecutionView }>) => void) | undefined
    vi.spyOn(apiClient, 'get').mockImplementationOnce(() => new Promise(resolve => { respond = resolve }))
    vi.spyOn(apiClient, 'delete').mockRejectedValue(new Error('归档失败'))
    const store = useExecutionsStore()
    const pending = store.loadExecution(record.id)
    await expect(store.deleteExecutions([record.id])).rejects.toThrow('归档失败')
    respond?.({ data: { execution: record } })
    await pending
    expect(store.active?.id).toBe(record.id)
    expect(store.executions.map(item => item.id)).toEqual([record.id])
  })

  it('reruns requested cases without promoting expanded dependencies', async () => {
    const created = {
      id: 'execution-rerun', project_id: 'project-1', state: 'QUEUED', execution_type: 'regression',
      source_revision_id: 'source-1', environment_revision_id: 'environment-1',
      environment_name: '生产环境（新）- 腾讯云', case_statuses: ['QUEUED'], case_results: [], summary: {},
      cancellation_requested: false, created_at: '', started_at: null, finished_at: null,
    } as ExecutionView
    const post = vi.spyOn(apiClient, 'post').mockResolvedValue({ data: { execution: created } })
    const store = useExecutionsStore()
    vi.spyOn(store, 'select').mockResolvedValue()

    const rerun = await store.rerunExecution({
      id: 'execution-source',
      project_id: 'project-1',
      source_revision_id: 'source-1',
      environment_revision_id: 'environment-1',
      environment_name: '生产环境（新）- 腾讯云',
      state: 'DONE',
      execution_type: 'regression',
      task_id: 'task-1',
      task_name: '收藏接口发版回归',
      case_statuses: ['PASSED', 'FAILED'],
      case_results: [
        { execution_case_id: 'case-1', case_version_id: 'case-version-1', execution_role: 'dependency', status: 'PASSED' },
        { execution_case_id: 'case-2', case_version_id: 'case-version-2', execution_role: 'requested', status: 'FAILED' },
      ],
      summary: {},
      cancellation_requested: false,
      created_at: '',
      started_at: null,
      finished_at: null,
    } as ExecutionView)

    expect(post).toHaveBeenCalledWith('/api/api-testing/v1/executions/execution-source/rerun', {
      case_version_ids: ['case-version-2'],
      idempotency_key: expect.any(String),
    })
    expect(rerun?.id).toBe('execution-rerun')
    expect(store.executions[0]?.id).toBe('execution-rerun')
  })
})
