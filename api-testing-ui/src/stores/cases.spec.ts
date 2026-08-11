import { beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'

import { apiClient } from '../api/client'
import type { CaseVersion } from '../api/contracts'
import { useCasesStore } from './cases'

const VERSION = {
  id: 'version-1', case_id: 'case-1', project_id: 'project-1', endpoint_id: 'endpoint-1',
  status: 'draft', origin: 'ai', version: 1, validation_summary: {},
  created_at: '2026-08-09T00:00:00Z', updated_at: '2026-08-09T00:00:00Z',
  name: '收藏列表', purpose: '验证收藏列表', priority: 'P1',
  request: { method: 'GET', path: '/favorite/list', service: 'default', path_params: {}, query: {}, headers: {}, cookies: {}, body: null },
  data_rows: [], assertions: [], extractions: [], dependencies: [], processing: { pre: [], post: [] },
} as unknown as CaseVersion

describe('cases store', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    vi.restoreAllMocks()
  })

  it('sends only the public CaseDraft fields when saving a loaded AI version', async () => {
    const post = vi.spyOn(apiClient, 'post').mockResolvedValue({ data: { case_version: { ...VERSION, version: 2 } } })
    const get = vi.spyOn(apiClient, 'get').mockResolvedValue({ data: { case_version: VERSION } })
    const store = useCasesStore()

    await store.loadVersion(VERSION.id)
    await store.save(VERSION.endpoint_id)

    expect(get).toHaveBeenCalledOnce()
    expect(post.mock.calls[0][1]).toEqual({
      case: expect.objectContaining({ name: '收藏列表', request: expect.any(Object) }),
    })
    expect(Object.keys((post.mock.calls[0][1] as { case: object }).case).sort()).toEqual([
      'assertions', 'data_rows', 'dependencies', 'extractions', 'name', 'priority', 'processing', 'purpose', 'request',
    ])
  })

  it('validates a saved draft against the selected environment snapshot', async () => {
    const get = vi.spyOn(apiClient, 'get').mockResolvedValue({ data: {
      environment_revision: {
        revision_id: 'environment-1',
        variables: { Biz: 'ZXB', ZXBToken: { configured: true, fingerprint: 'safe-fingerprint' } },
        services: { default: { name: 'default', base_url: 'https://api.example.test/app', unresolved: false } },
      },
    } })
    const post = vi.spyOn(apiClient, 'post')
      .mockResolvedValueOnce({ data: { case_version: { ...VERSION, version: 2 } } })
      .mockResolvedValueOnce({ data: { validation: { valid: true, errors: [], warnings: [] } } })
    const store = useCasesStore()
    store.registerVersion(VERSION)

    await store.save(VERSION.endpoint_id, 'environment-1')

    expect(get).toHaveBeenCalledWith('/api/api-testing/v1/environment-revisions/environment-1')
    expect(post.mock.calls[1]).toEqual([
      '/api/api-testing/v1/case-versions/version-1/validate',
      { environment_metadata: {
        variables: { Biz: 'ZXB', ZXBToken: { configured: true, fingerprint: 'safe-fingerprint' } },
        services: { default: { name: 'default', base_url: 'https://api.example.test/app', unresolved: false } },
      } },
    ])
  })

  it('restores every persisted case version for the saved source revision', async () => {
    const second = { ...VERSION, id: 'version-2', case_id: 'case-2', name: '收藏列表鉴权失败' }
    const get = vi.spyOn(apiClient, 'get').mockResolvedValue({ data: { case_versions: [VERSION, second] } })
    const store = useCasesStore()

    await store.loadSavedCases('source-revision-1')

    expect(get).toHaveBeenCalledWith('/api/api-testing/v1/cases?source_revision_id=source-revision-1')
    expect(store.versionIdsByEndpoint['endpoint-1']).toEqual(['version-1', 'version-2'])
    expect(store.activeVersionByEndpoint['endpoint-1']).toBe('version-1')
  })

  it('replaces the active version of the same case without inflating the case count', () => {
    const store = useCasesStore()
    store.registerVersion(VERSION)
    store.registerVersion({ ...VERSION, id: 'version-2', version: 2 })

    expect(store.versionIdsByEndpoint['endpoint-1']).toEqual(['version-2'])
    expect(store.activeVersionByEndpoint['endpoint-1']).toBe('version-2')
  })

  it('makes a long-running AI job resumable after the local polling window', async () => {
    vi.spyOn(apiClient, 'get').mockResolvedValue({
      data: { job: { id: 'job-1', state: 'running', endpoint_ids: [], requested_model: 'qwen', actual_model: 'qwen', fallback_used: false, summary: {}, batches: [] } },
    })
    const store = useCasesStore()

    await store.pollAiJob('job-1', { maxAttempts: 1, delayMs: 0 })

    expect(store.aiPolling).toBe(false)
    expect(store.aiCanResume).toBe(true)
    expect(store.aiError).toContain('继续查看')
  })

  it('restores the latest unfinished AI job after a page reload', async () => {
    const get = vi.spyOn(apiClient, 'get').mockResolvedValue({ data: {
      job: { id: 'job-9', state: 'running', endpoint_ids: ['endpoint-1'], requested_model: 'qwen', actual_model: 'qwen', fallback_used: false, summary: {}, batches: [] },
    } })
    const store = useCasesStore()

    await store.restoreLatestAiJob('project-1')

    expect(get).toHaveBeenCalledWith('/api/api-testing/v1/ai-jobs/latest?project_id=project-1')
    expect(store.lastAiJobId).toBe('job-9')
    expect(store.aiCanResume).toBe(true)
  })

  it('attaches AI generation and debug execution to the current task', async () => {
    const post = vi.spyOn(apiClient, 'post')
      .mockResolvedValueOnce({ data: { job: { id: 'job-1', state: 'queued', batches: [] } } })
      .mockResolvedValueOnce({ data: { execution: { id: 'execution-1', state: 'QUEUED' } } })
    const store = useCasesStore()
    vi.spyOn(store, 'pollAiJob').mockResolvedValue()
    vi.spyOn(store, 'pollExecution').mockResolvedValue()

    await store.generate(['endpoint-1'], 'environment-1', '覆盖收藏流程', 'task-1')
    await store.debug({
      projectId: 'project-1', sourceRevisionId: 'source-1',
      environmentRevisionId: 'environment-1', caseVersionId: 'version-1', taskId: 'task-1',
    })

    expect(post.mock.calls[0][1]).toEqual(expect.objectContaining({ task_id: 'task-1' }))
    expect(post.mock.calls[1][1]).toEqual(expect.objectContaining({ task_id: 'task-1' }))
  })

  it('keeps real execution trace and error evidence in debug results', async () => {
    vi.spyOn(apiClient, 'get').mockResolvedValue({ data: { execution: {
      id: 'execution-1', state: 'DONE', case_statuses: ['BROKEN'], summary: {},
      case_results: [{
        execution_case_id: 'execution-case-1', case_version_id: 'version-1', endpoint_id: 'endpoint-1',
        status: 'BROKEN', failure_category: 'network', duration_ms: 33,
        sanitized_result: { error_message: '连接超时', trace: [{ phase: 'request', message: '连接目标服务' }] },
      }],
    } } })
    const store = useCasesStore()

    await store.pollExecution('execution-1', { maxAttempts: 1, delayMs: 0 })

    expect(store.debugResult?.logs.join('\n')).toContain('连接超时')
    expect(store.debugResult?.logs.join('\n')).toContain('连接目标服务')
  })

  it('keeps a timed-out debug execution resumable instead of starting a duplicate', async () => {
    vi.spyOn(apiClient, 'get').mockResolvedValue({ data: { execution: {
      id: 'execution-1', state: 'RUNNING', case_statuses: ['RUNNING'], case_results: [], summary: {},
    } } })
    const store = useCasesStore()

    await store.pollExecution('execution-1', { maxAttempts: 1, delayMs: 0 })

    expect(store.debugPolling).toBe(false)
    expect(store.debugCanResume).toBe(true)
    expect(store.debugExecution?.id).toBe('execution-1')
  })
})
