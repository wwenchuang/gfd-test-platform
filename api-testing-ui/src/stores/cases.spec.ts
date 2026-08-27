import { beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'

import { apiClient } from '../api/client'
import type { ApiEndpoint, CaseVersion, GeneratedCasePreview } from '../api/contracts'
import { useCasesStore } from './cases'

const VERSION = {
  id: 'version-1', case_id: 'case-1', project_id: 'project-1', endpoint_id: 'endpoint-1',
  status: 'draft', origin: 'ai', version: 1, group_name: '', validation_summary: {},
  created_at: '2026-08-09T00:00:00Z', updated_at: '2026-08-09T00:00:00Z',
  name: '收藏列表', purpose: '验证收藏列表', priority: 'P1',
  business: 'shared',
  request: { method: 'GET', path: '/favorite/list', service: 'default', path_params: {}, query: {}, headers: {}, cookies: {}, body: null },
  data_rows: [], assertions: [], extractions: [], dependencies: [], processing: { pre: [], post: [] },
} as unknown as CaseVersion

describe('cases store', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    vi.restoreAllMocks()
  })

  it('starts a new draft with the direct OpenAPI JSON body example', () => {
    const store = useCasesStore()
    const endpoint = {
      id: 'endpoint-add', method: 'POST', path: '/favorite/add', summary: '添加收藏', tags: [],
      operation: {
        requestBody: { content: { 'application/json': { example: { modelSn: 'm001' } } } },
      },
    } as ApiEndpoint

    const draft = store.draftFor(endpoint)

    expect(draft.request.body).toEqual({ modelSn: 'm001' })
  })

  it('starts a new draft with the first named OpenAPI JSON body example', () => {
    const store = useCasesStore()
    const endpoint = {
      id: 'endpoint-cancel', method: 'POST', path: '/favorite/cancel', summary: '取消收藏', tags: [],
      operation: {
        requestBody: { content: { 'application/json': { examples: {
          model: { value: { modelSn: 'm002', favoriteType: 'MODEL' } },
        } } } },
      },
    } as ApiEndpoint

    const draft = store.draftFor(endpoint)

    expect(draft.request.body).toEqual({ modelSn: 'm002', favoriteType: 'MODEL' })
  })

  it('starts a new draft with an example from a referenced OpenAPI request body', () => {
    const store = useCasesStore()
    const endpoint = {
      id: 'endpoint-referenced', method: 'POST', path: '/favorite/referenced', summary: '引用请求体', tags: [],
      operation: {
        requestBody: { $ref: '#/components/requestBodies/FavoriteRequest' },
        resolved_dependencies: {
          '#/components/requestBodies/FavoriteRequest': {
            content: { 'application/json': { example: { modelSn: 'm003' } } },
          },
        },
      },
    } as ApiEndpoint

    const draft = store.draftFor(endpoint)

    expect(draft.request.body).toEqual({ modelSn: 'm003' })
  })

  it('starts a new draft with OpenAPI query parameter examples but no header scenarios', () => {
    const store = useCasesStore()
    const endpoint = {
      id: 'endpoint-work-info', method: 'GET', path: '/devices/workInfo', summary: '设备工作详情', tags: [],
      operation: {
        parameters: [
          { name: 'deviceSn', in: 'query', schema: { type: 'string' }, example: '1234567890123456789' },
          { name: 'source', in: 'query', schema: { type: 'string', default: 'calibration' } },
          { name: 'Authorization', in: 'header', schema: { type: 'string' }, example: 'Bearer token' },
        ],
      },
    } as ApiEndpoint

    const draft = store.draftFor(endpoint)

    expect(draft.request.query).toEqual({
      deviceSn: '1234567890123456789',
      source: 'calibration',
    })
    expect(draft.request.headers).toEqual({})
  })

  it('starts a new draft with JSON body values derived from property examples', () => {
    const store = useCasesStore()
    const endpoint = {
      id: 'endpoint-add', method: 'POST', path: '/collection/add', summary: '添加修改收藏', tags: [],
      operation: {
        requestBody: {
          content: {
            'application/json': {
              schema: {
                type: 'object',
                properties: {
                  modelSn: { type: 'string', example: 'm001' },
                  type: { type: 'string', default: 'MODEL' },
                },
              },
            },
          },
        },
      },
    } as ApiEndpoint

    const draft = store.draftFor(endpoint)

    expect(draft.request.body).toEqual({ modelSn: 'm001', type: 'MODEL' })
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
      'assertions', 'business', 'data_rows', 'dependencies', 'extractions', 'name', 'priority', 'processing', 'purpose', 'request',
    ])
  })

  it('removes read-only nested sequence fields before resaving a persisted version', async () => {
    const persisted = {
      ...VERSION,
      data_rows: [{ name: '默认数据', values: {}, enabled: true, sequence: 0 }],
      assertions: [{ type: 'status_code', operator: 'equals', expected: 200, path: null, name: null, timeout_ms: 0, enabled: true, sequence: 0 }],
    } as unknown as CaseVersion
    vi.spyOn(apiClient, 'get').mockResolvedValue({ data: {
      environment_revision: { revision_id: 'environment-1', variables: {}, services: {} },
    } })
    const post = vi.spyOn(apiClient, 'post')
      .mockResolvedValueOnce({ data: { case_version: { ...persisted, id: 'version-2', version: 2 } } })
      .mockResolvedValueOnce({ data: { validation: { valid: true, errors: [], warnings: [] } } })
    const store = useCasesStore()
    store.registerVersion(persisted)

    await store.saveForDebug(VERSION.endpoint_id, 'environment-1')

    const submitted = (post.mock.calls[0][1] as { case: CaseVersion }).case
    expect(submitted.data_rows[0]).not.toHaveProperty('sequence')
    expect(submitted.assertions[0]).not.toHaveProperty('sequence')
    expect(submitted.assertions[0]).not.toHaveProperty('path')
    expect(submitted.assertions[0]).not.toHaveProperty('name')
  })

  it('validates a saved draft against the selected environment snapshot', async () => {
    const get = vi.spyOn(apiClient, 'get').mockResolvedValue({ data: {
      environment_revision: {
        revision_id: 'environment-1',
        variables: { Biz: 'ZXB', ZXBToken: { configured: true, fingerprint: 'safe-fingerprint' } },
        services: { default: { name: 'default', base_url: 'https://api.example.test/app', unresolved: false } },
        default_headers: { Authorization: 'Bearer {{ZXBToken}}' },
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
        headers: { Authorization: { configured: true } },
      } },
    ])
  })

  it('prepares the current draft for debug with the newly saved version', async () => {
    const saved = { ...VERSION, id: 'version-2', version: 2 }
    const get = vi.spyOn(apiClient, 'get').mockResolvedValue({ data: {
      environment_revision: { revision_id: 'environment-1', variables: {}, services: {} },
    } })
    const post = vi.spyOn(apiClient, 'post')
      .mockResolvedValueOnce({ data: { case_version: saved } })
      .mockResolvedValueOnce({ data: { validation: { valid: true, errors: [], warnings: [] } } })
    const store = useCasesStore()
    store.registerVersion(VERSION)
    store.drafts[VERSION.endpoint_id] = {
      ...JSON.parse(JSON.stringify(store.drafts[VERSION.endpoint_id])),
      assertions: [{ type: 'status_code', operator: 'equals', expected: 201, timeout_ms: 0, enabled: true }],
    }

    const prepared = await store.saveForDebug(VERSION.endpoint_id, 'environment-1')

    expect(prepared.id).toBe('version-2')
    expect(store.activeVersionByEndpoint[VERSION.endpoint_id]).toBe('version-2')
    expect(get).toHaveBeenCalledOnce()
    expect(post.mock.calls[0][1]).toEqual({ case: expect.objectContaining({
      assertions: [expect.objectContaining({ expected: 201 })],
    }) })
  })

  it('does not save or debug a draft with a business code entered as an HTTP status', async () => {
    const post = vi.spyOn(apiClient, 'post')
    const store = useCasesStore()
    store.registerVersion(VERSION)
    store.drafts[VERSION.endpoint_id] = {
      ...JSON.parse(JSON.stringify(store.drafts[VERSION.endpoint_id])),
      assertions: [{ type: 'status_code', operator: 'equals', expected: 60101004, timeout_ms: 0, enabled: true }],
    }

    await expect(store.saveForDebug(VERSION.endpoint_id, 'environment-1')).rejects.toThrow('响应 JSON 字段')

    expect(post).not.toHaveBeenCalled()
    expect(store.validationErrors['assertions[0].expected']).toContain('100 到 599')
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

  it('previews basic positive drafts without registering persisted versions', async () => {
    const preview = {
      id: 'basic-positive-endpoint-1',
      endpoint_id: 'endpoint-1',
      origin: 'imported',
      case: { ...VERSION, name: '收藏列表 - 基础正向流程' },
    } as unknown as GeneratedCasePreview
    const post = vi.spyOn(apiClient, 'post').mockResolvedValue({ data: { case_previews: [preview] } })
    const store = useCasesStore()

    const previews = await store.previewBasicPositive(['endpoint-1'], 'environment-1', 'task-1')

    expect(post).toHaveBeenCalledWith('/api/api-testing/v1/cases/basic-positive/preview', {
      endpoint_ids: ['endpoint-1'],
      environment_revision_id: 'environment-1',
      task_id: 'task-1',
    })
    expect(previews).toEqual([preview])
    expect(store.generatedPreviews).toEqual([preview])
    expect(store.versions).toEqual({})
    expect(store.activeVersionByEndpoint['endpoint-1']).toBeUndefined()
    expect(store.savedMessage).toBe('已生成 1 个基础正向候选，请确认后保存')
    expect(store.basicGenerating).toBe(false)
  })

  it('saves one generated preview as an imported case and removes it from previews', async () => {
    const preview = {
      id: 'basic-positive-endpoint-1',
      endpoint_id: 'endpoint-1',
      origin: 'imported',
      case: {
        name: '收藏列表 - 基础正向流程',
        purpose: '验证收藏列表',
        priority: 'P1',
        request: { method: 'GET', path: '/favorite/list', service: 'default', path_params: {}, query: {}, headers: {}, cookies: {}, body: null },
        data_rows: [],
        assertions: [],
        extractions: [],
        dependencies: [],
        processing: { pre: [], post: [] },
      },
    } as GeneratedCasePreview
    const saved = { ...VERSION, id: 'version-basic', case_id: 'case-basic', origin: 'imported', name: preview.case.name }
    const post = vi.spyOn(apiClient, 'post').mockResolvedValue({ data: { case_version: saved } })
    const store = useCasesStore()
    store.generatedPreviews = [preview]

    const version = await store.saveGeneratedPreview(preview.id)

    expect(post).toHaveBeenCalledWith('/api/api-testing/v1/cases', {
      endpoint_id: 'endpoint-1',
      case: preview.case,
      origin: 'imported',
    })
    expect(version).toEqual(saved)
    expect(store.generatedPreviews).toEqual([])
    expect(store.versions['version-basic']).toEqual(saved)
    expect(store.activeVersionByEndpoint['endpoint-1']).toBe('version-basic')
    expect(store.savedMessage).toBe('基础正向用例已保存')
  })

  it('moves selected versions sequentially and reports partial progress', async () => {
    const first = { ...VERSION, group_name: '发版回归' }
    const put = vi.spyOn(apiClient, 'put')
      .mockResolvedValueOnce({ data: { case_version: first } })
      .mockRejectedValueOnce(new Error('network unavailable'))
    const store = useCasesStore()
    store.registerVersion(VERSION)

    await expect(store.updateVersionGroups(['version-1', 'version-2', 'version-3'], '发版回归'))
      .rejects.toThrow('已完成 1/3，失败用例 version-2')

    expect(put.mock.calls).toEqual([
      ['/api/api-testing/v1/case-versions/version-1/group', { group_name: '发版回归' }],
      ['/api/api-testing/v1/case-versions/version-2/group', { group_name: '发版回归' }],
    ])
    expect(store.versions['version-1'].group_name).toBe('发版回归')
    expect(store.savedMessage).toContain('已移动 1/3 条')
    expect(store.saving).toBe(false)
  })

  it('replaces the active version of the same case without inflating the case count', () => {
    const store = useCasesStore()
    store.registerVersion(VERSION)
    store.registerVersion({ ...VERSION, id: 'version-2', version: 2 })

    expect(store.versionIdsByEndpoint['endpoint-1']).toEqual(['version-2'])
    expect(store.activeVersionByEndpoint['endpoint-1']).toBe('version-2')
  })

  it('archives the selected saved case and activates the next available version', async () => {
    const post = vi.spyOn(apiClient, 'post')
    const del = vi.spyOn(apiClient, 'delete').mockResolvedValue({ data: { case: { id: 'case-1', status: 'archived' } } })
    const store = useCasesStore()
    const second = { ...VERSION, id: 'version-2', case_id: 'case-2', name: '取消收藏' }
    store.registerVersion(VERSION)
    store.registerVersion(second, false)

    await store.archiveCase(VERSION.endpoint_id, VERSION.id)

    expect(del).toHaveBeenCalledWith('/api/api-testing/v1/cases/case-1')
    expect(post).not.toHaveBeenCalled()
    expect(store.versionIdsByEndpoint[VERSION.endpoint_id]).toEqual(['version-2'])
    expect(store.activeVersionByEndpoint[VERSION.endpoint_id]).toBe('version-2')
    expect(store.drafts[VERSION.endpoint_id].name).toBe('取消收藏')
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
        sanitized_result: { error_message: '连接超时', trace: [
          { phase: 'request', message: '连接目标服务' },
          { phase: 'workflow_step', stage: 'setup', index: 0, name: '查询模型', status: 'PASSED', request: {}, response: {}, assertions: [], extracted_variables: { modelSn: '***' } },
          { phase: 'workflow_step', stage: 'cleanup', index: 0, name: '取消打印', status: 'FAILED', failure_category: 'network', request: {}, response: {}, assertions: [], extracted_variables: {}, error_message: '取消请求超时', attempt: 2, max_attempts: 2 },
        ] },
      }],
    } } })
    const store = useCasesStore()

    await store.pollExecution('execution-1', { maxAttempts: 1, delayMs: 0 })

    expect(store.debugResult?.logs.join('\n')).toContain('连接超时')
    expect(store.debugResult?.logs.join('\n')).toContain('连接目标服务')
    expect(store.debugResult?.durationMs).toBe(33)
    expect(store.debugResult?.errorMessage).toBe('连接超时')
    expect(store.debugResult?.trace).toEqual([
      expect.objectContaining({ stage: 'setup', index: 0, name: '查询模型', status: 'PASSED', extractedVariableNames: ['modelSn'] }),
      expect.objectContaining({ stage: 'cleanup', index: 0, name: '取消打印', status: 'FAILED', attempt: 2, maxAttempts: 2 }),
    ])
  })

  it('shows the requested debug case instead of an expanded dependency result', async () => {
    vi.spyOn(apiClient, 'get').mockResolvedValue({ data: { execution: {
      id: 'execution-1', state: 'DONE', case_statuses: ['PASSED', 'FAILED'], summary: {},
      case_results: [
        {
          execution_case_id: 'dependency-case', case_version_id: 'setup-version', endpoint_id: 'setup-endpoint',
          execution_role: 'dependency', status: 'PASSED', failure_category: '', duration_ms: 12, sanitized_result: {},
        },
        {
          execution_case_id: 'requested-case', case_version_id: 'target-version', endpoint_id: 'target-endpoint',
          execution_role: 'requested', status: 'FAILED', failure_category: 'product_assertion', duration_ms: 18,
          sanitized_result: { error_message: '主体断言失败' },
        },
      ],
    } } })
    const store = useCasesStore()

    await store.pollExecution('execution-1', { maxAttempts: 1, delayMs: 0 })

    expect(store.debugResult?.executionCaseId).toBe('requested-case')
    expect(store.debugResult?.logs.join('\n')).toContain('主体断言失败')
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

  it('clears stale debug evidence when the execution environment changes', () => {
    const store = useCasesStore()
    store.debugExecution = { id: 'execution-old' } as never
    store.debugResult = {
      status: 'FAILED', executionCaseId: 'case-old', durationMs: 10, errorMessage: '', trace: [], resolvedRequest: {},
      sanitizedResponse: {}, assertions: [], failureCategory: 'product_assertion', logs: [],
    }
    store.debugError = '旧环境错误'
    store.debugCanResume = true

    store.clearDebug()

    expect(store.debugExecution).toBeNull()
    expect(store.debugResult).toBeNull()
    expect(store.debugError).toBe('')
    expect(store.debugCanResume).toBe(false)
  })

  it('exposes visible progress and success after adopting a passing debug result', async () => {
    vi.spyOn(apiClient, 'post').mockResolvedValue({ data: { baseline: { id: 'baseline-1', status: 'active' } } })
    const store = useCasesStore()

    const adoption = store.adoptBaseline('version-1', 'execution-case-1')

    expect(store.baselineAdopting).toBe(true)
    await adoption
    expect(store.baselineAdopting).toBe(false)
    expect(store.baselineMessage).toBe('已采纳为基线')
    expect(store.baselineError).toBe('')
  })

  it('exposes the backend error when baseline adoption is rejected', async () => {
    vi.spyOn(apiClient, 'post').mockRejectedValue(new Error('调试证据与当前用例版本不一致'))
    const store = useCasesStore()

    await store.adoptBaseline('version-1', 'execution-case-1')

    expect(store.baselineAdopting).toBe(false)
    expect(store.baselineMessage).toBe('')
    expect(store.baselineError).toBe('调试证据与当前用例版本不一致')
  })

  it('does not restore a late debug response after the environment changed', async () => {
    let finishRequest!: (value: unknown) => void
    vi.spyOn(apiClient, 'get').mockReturnValue(new Promise(resolve => { finishRequest = resolve }) as never)
    const store = useCasesStore()

    const polling = store.pollExecution('execution-old', { maxAttempts: 1, delayMs: 0 })
    store.clearDebug()
    finishRequest({ data: { execution: {
      id: 'execution-old', state: 'DONE', case_statuses: ['FAILED'], summary: {},
      case_results: [{
        execution_case_id: 'case-old', case_version_id: 'version-1', endpoint_id: 'endpoint-1',
        status: 'FAILED', failure_category: 'product_assertion', duration_ms: 10, sanitized_result: {},
      }],
    } } })
    await polling

    expect(store.debugExecution).toBeNull()
    expect(store.debugResult).toBeNull()
  })
})
