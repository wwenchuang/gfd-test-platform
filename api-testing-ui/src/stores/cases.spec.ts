import { beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'

import { apiClient, type ApiClient } from '../api/client'
import type { AiJob, ApiEndpoint, CaseVersion, GeneratedCasePreview } from '../api/contracts'
import { useCasesStore } from './cases'

const VERSION = {
  id: 'version-1', case_id: 'case-1', project_id: 'project-1', endpoint_id: 'endpoint-1',
  status: 'draft', origin: 'ai', version: 1, group_name: '', validation_summary: {},
  created_at: '2026-08-09T00:00:00Z', updated_at: '2026-08-09T00:00:00Z',
  name: '收藏列表', purpose: '验证收藏列表', priority: 'P1',
  app_package: 'com.kfb.model', app_name: '智小白3D',
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
      endpoint_id: 'endpoint-1',
      case: expect.objectContaining({ name: '收藏列表', request: expect.any(Object) }),
    })
    expect(Object.keys((post.mock.calls[0][1] as { case: object }).case).sort()).toEqual([
      'app_name', 'app_package', 'assertions', 'business', 'data_rows', 'dependencies', 'extractions', 'name', 'priority', 'processing', 'purpose', 'request',
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

  it('keeps a persisted draft successful when post-save validation is temporarily unavailable', async () => {
    const saved = { ...VERSION, id: 'version-2', version: 2 }
    const post = vi.spyOn(apiClient, 'post')
      .mockResolvedValueOnce({ data: { case_version: saved } })
      .mockRejectedValueOnce(new Error('服务响应超时（30 秒）'))
    const store = useCasesStore()
    store.registerVersion(VERSION)

    await expect(store.save(VERSION.endpoint_id)).resolves.toMatchObject({ id: 'version-2' })

    expect(post).toHaveBeenCalledTimes(2)
    expect(store.savedMessage).toContain('草稿 v2 已保存')
    expect(store.savedMessage).toContain('校验暂未完成')
    expect(store.saving).toBe(false)
  })

  it('does not start debugging when post-save validation is unavailable', async () => {
    const saved = { ...VERSION, id: 'version-2', version: 2 }
    vi.spyOn(apiClient, 'get').mockResolvedValue({ data: {
      environment_revision: { revision_id: 'environment-1', variables: {}, services: {} },
    } })
    vi.spyOn(apiClient, 'post')
      .mockResolvedValueOnce({ data: { case_version: saved } })
      .mockRejectedValueOnce(new Error('服务响应超时（30 秒）'))
    const store = useCasesStore()
    store.registerVersion(VERSION)

    await expect(store.saveForDebug(VERSION.endpoint_id, 'environment-1')).rejects.toThrow('草稿已保存，但保存后校验未完成')
    expect(store.activeVersionByEndpoint[VERSION.endpoint_id]).toBe('version-2')
    expect(store.saving).toBe(false)
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
    expect(post.mock.calls[0][1]).toEqual({ endpoint_id: 'endpoint-1', case: expect.objectContaining({
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

  it('blocks imprecise business code assertions before saving the draft', async () => {
    const post = vi.spyOn(apiClient, 'post')
    const store = useCasesStore()
    store.registerVersion(VERSION)
    store.drafts[VERSION.endpoint_id] = {
      ...JSON.parse(JSON.stringify(store.drafts[VERSION.endpoint_id])),
      assertions: [{ type: 'json_path', path: '$.code', operator: 'not_equals', expected: 0, timeout_ms: 0, enabled: true }],
    }

    await expect(store.saveForDebug(VERSION.endpoint_id, 'environment-1')).rejects.toThrow('精确')

    expect(post).not.toHaveBeenCalled()
    expect(store.validationErrors['assertions[0].operator']).toContain('等于')
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

  it('projects a historical logical case onto the current endpoint and adapts it on save', async () => {
    const currentEndpoint = {
      id: 'endpoint-current', method: 'POST', path: '/favorite/list/v2', summary: '收藏列表', tags: [],
    } as ApiEndpoint
    const historical = {
      ...VERSION,
      endpoint_id: 'endpoint-old',
      current_endpoint_id: currentEndpoint.id,
      source_state: 'needs_adaptation',
    } as CaseVersion
    const adapted = {
      ...historical,
      id: 'version-2', version: 2, endpoint_id: currentEndpoint.id,
      current_endpoint_id: currentEndpoint.id, source_state: 'current',
      request: { ...historical.request, method: currentEndpoint.method, path: currentEndpoint.path },
    } as CaseVersion
    const post = vi.spyOn(apiClient, 'post')
      .mockResolvedValueOnce({ data: { case_version: adapted } })
      .mockResolvedValueOnce({ data: { validation: { valid: true, errors: [], warnings: [] } } })
    const store = useCasesStore()

    store.registerVersion(historical, false)
    const draft = store.draftFor(currentEndpoint)

    expect(store.versionIdsByEndpoint[currentEndpoint.id]).toEqual([historical.id])
    expect(draft.request.method).toBe('POST')
    expect(draft.request.path).toBe('/favorite/list/v2')

    await store.save(currentEndpoint.id)

    expect(post.mock.calls[0][1]).toEqual({
      endpoint_id: currentEndpoint.id,
      case: expect.objectContaining({ request: expect.objectContaining({ path: '/favorite/list/v2' }) }),
    })
    expect(store.activeVersionByEndpoint[currentEndpoint.id]).toBe('version-2')
    expect(store.versions['version-1']).toBeUndefined()
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

  it('does not let an older AI generation result replace a newer debugged baseline version', async () => {
    const current = {
      ...VERSION,
      id: 'version-2',
      version: 2,
      lifecycle: {
        debug_status: 'PASSED',
        debug_execution_id: 'execution-debug',
        baseline_status: 'active',
        baseline_id: 'baseline-1',
      },
    } as CaseVersion
    const completedJob = {
      id: 'job-completed', state: 'completed', endpoint_ids: ['endpoint-1'],
      requested_model: 'qwen', actual_model: 'qwen', fallback_used: false, summary: {},
      batches: [{
        id: 'batch-1', sequence: 1, state: 'completed', endpoint_ids: ['endpoint-1'],
        requested_model: 'qwen', actual_model: 'qwen', fallback_used: false, fallback_reason: '',
        generated_draft_ids: ['version-1'], validation_errors: [],
      }],
    }
    vi.spyOn(apiClient, 'get')
      .mockResolvedValueOnce({ data: { job: completedJob } })
      .mockResolvedValueOnce({ data: { case_version: VERSION } })
    const store = useCasesStore()
    store.registerVersion(current)

    await store.restoreLatestAiJob('project-1', 'source-1')

    expect(store.versionIdsByEndpoint['endpoint-1']).toEqual(['version-2'])
    expect(store.activeVersionByEndpoint['endpoint-1']).toBe('version-2')
    expect(store.versions['version-2'].lifecycle).toMatchObject({
      debug_status: 'PASSED', baseline_status: 'active',
    })
    expect(store.versions['version-1']).toBeUndefined()
  })

  it('keeps lifecycle evidence when the same version is refreshed by a metadata-only response', () => {
    const store = useCasesStore()
    store.registerVersion({
      ...VERSION,
      lifecycle: {
        debug_status: 'PASSED',
        debug_execution_id: 'execution-debug',
        baseline_status: 'active',
        baseline_id: 'baseline-1',
      },
    } as CaseVersion)

    store.registerVersion({ ...VERSION, group_name: '发版回归', lifecycle: {} } as CaseVersion, false)

    expect(store.versions[VERSION.id].group_name).toBe('发版回归')
    expect(store.versions[VERSION.id].lifecycle).toMatchObject({
      debug_status: 'PASSED', baseline_status: 'active', baseline_id: 'baseline-1',
    })
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

  it('keeps a readable validation reason when AI generation ends in failure', async () => {
    vi.spyOn(apiClient, 'get').mockResolvedValue({ data: {
      job: {
        id: 'job-failed', state: 'failed_validation', endpoint_ids: ['endpoint-1'],
        requested_model: 'qwen', actual_model: 'qwen', fallback_used: false, summary: {},
        batches: [{
          id: 'batch-1', sequence: 1, state: 'failed_validation', endpoint_ids: ['endpoint-1'],
          requested_model: 'qwen', actual_model: 'qwen', fallback_used: false, fallback_reason: '',
          generated_draft_ids: [], validation_errors: [{ code: 'missing_endpoint_coverage', message: 'missing endpoint coverage' }],
        }],
      },
    } })
    const store = useCasesStore()

    await store.pollAiJob('job-failed', { maxAttempts: 1, delayMs: 0 })

    expect(store.aiCanResume).toBe(false)
    expect(store.aiError).toContain('部分已选接口没有生成有效用例')
  })

  it('requests and restores a saved Qwen diagnosis for one validation error', async () => {
    const diagnosedJob = {
      id: 'job-failed', state: 'failed_validation', endpoint_ids: ['endpoint-1'],
      requested_model: 'qwen', actual_model: 'qwen3.7-plus', fallback_used: false, summary: {},
      batches: [{
        id: 'batch-1', sequence: 1, state: 'failed_validation', endpoint_ids: ['endpoint-1'],
        requested_model: 'qwen', actual_model: 'qwen3.7-plus', fallback_used: false, fallback_reason: '',
        generated_draft_ids: [],
        validation_errors: [{
          code: 'candidate_validation_error', message: 'must constrain response fields',
          diagnosis: { model: 'qwen3.7-plus', analysis: { summary: '断言范围不明确' } },
        }],
      }],
    } as AiJob
    const post = vi.spyOn(apiClient, 'post').mockResolvedValue({ data: { job: diagnosedJob } })
    const store = useCasesStore()
    store.aiJob = { ...diagnosedJob, batches: [] }

    await store.diagnoseAiValidation('batch-1', 0)

    expect(post).toHaveBeenCalledWith('/api/api-testing/v1/ai-jobs/job-failed/validation-diagnosis', {
      batch_id: 'batch-1', error_index: 0,
    })
    expect(store.aiJob).toEqual(diagnosedJob)
    expect(store.aiDiagnosisBatchId).toBe('')
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

  it('ignores a stale AI restoration response after the source changes', async () => {
    let resolveOld!: (value: { data: { job: Record<string, unknown> } }) => void
    const oldClient = {
      get: vi.fn(() => new Promise<{ data: { job: Record<string, unknown> } }>(resolve => { resolveOld = resolve })),
    } as unknown as Pick<ApiClient, 'get'>
    const newJob = {
      id: 'job-new', state: 'running', endpoint_ids: ['endpoint-2'],
      requested_model: 'qwen', actual_model: 'qwen', fallback_used: false, summary: {}, batches: [],
    }
    const oldJob = { ...newJob, id: 'job-old', endpoint_ids: ['endpoint-1'] }
    const newClient = { get: vi.fn().mockResolvedValue({ data: { job: newJob } }) } as unknown as Pick<ApiClient, 'get'>
    const store = useCasesStore()

    const oldRestore = store.restoreLatestAiJob('project-1', 'source-old', oldClient)
    await store.restoreLatestAiJob('project-1', 'source-new', newClient)
    resolveOld({ data: { job: oldJob } })
    await oldRestore

    expect(store.aiJob?.id).toBe('job-new')
    expect(store.lastAiJobId).toBe('job-new')
  })

  it('clears the previous AI result while restoring a revision with no AI job', async () => {
    const store = useCasesStore()
    store.aiJob = { id: 'old-job', state: 'completed', batches: [], endpoint_ids: [], requested_model: 'qwen', actual_model: 'qwen', fallback_used: false, summary: {} }
    store.lastAiJobId = 'old-job'
    store.aiError = '旧版本的失败'
    let finish!: (value: unknown) => void
    const client = { get: vi.fn(() => new Promise(resolve => { finish = resolve })) } as unknown as Pick<ApiClient, 'get'>
    const pending = store.restoreLatestAiJob('project-1', 'source-new', client)
    expect(store.aiJob).toBeNull()
    expect(store.aiError).toBe('')
    finish({ data: { job: null } })
    await pending
    expect(store.aiJob).toBeNull()
    expect(store.lastAiJobId).toBe('')
  })

  it('ignores an old polling response and its generated versions after switching scope', async () => {
    const store = useCasesStore()
    let finish!: (value: unknown) => void
    vi.spyOn(apiClient, 'get').mockImplementation(() => new Promise(resolve => { finish = resolve }) as never)
    const load = vi.spyOn(store, 'loadVersion').mockResolvedValue()
    const poll = store.pollAiJob('old-job', { maxAttempts: 1, delayMs: 0 })
    await store.restoreLatestAiJob('project-new', 'source-new', { get: vi.fn().mockResolvedValue({ data: { job: null } }) })
    finish({ data: { job: { id: 'old-job', state: 'completed', batches: [{ generated_draft_ids: ['old-version'] }] } } })
    await poll
    expect(store.aiJob).toBeNull()
    expect(store.aiError).toBe('')
    expect(load).not.toHaveBeenCalled()
  })

  it('does not register delayed generated versions or errors into a new polling session', async () => {
    const store = useCasesStore()
    let failOld!: (error: Error) => void
    vi.spyOn(apiClient, 'get')
      .mockResolvedValueOnce({ data: { job: { id: 'old-job', state: 'completed', batches: [{ generated_draft_ids: ['old-version'] }] } } } as never)
      .mockImplementationOnce(() => new Promise((_, reject) => { failOld = reject }) as never)
      .mockImplementationOnce(() => new Promise(() => {}) as never)
    const oldPoll = store.pollAiJob('old-job', { maxAttempts: 1, delayMs: 0 })
    await vi.waitFor(() => expect(apiClient.get).toHaveBeenCalledTimes(2))
    store.clearAiJob()
    void store.pollAiJob('new-job', { maxAttempts: 1, delayMs: 0 })
    failOld(new Error('old scope error'))
    await oldPoll
    expect(store.aiError).toBe('')
    expect(store.aiPolling).toBe(true)
    expect(store.lastAiJobId).toBe('new-job')
    expect(store.versions['old-version']).toBeUndefined()
  })

  it('ignores a delayed diagnosis after the source has changed', async () => {
    const store = useCasesStore()
    store.aiJob = { id: 'old-job', state: 'completed', batches: [], endpoint_ids: [], requested_model: 'qwen', actual_model: 'qwen', fallback_used: false, summary: {} }
    let finish!: (value: unknown) => void
    vi.spyOn(apiClient, 'post').mockImplementation(() => new Promise(resolve => { finish = resolve }) as never)
    const diagnosis = store.diagnoseAiValidation('old-batch', 0)
    store.clearAiJob()
    store.aiDiagnosisBatchId = 'new-batch'
    finish({ data: { job: { id: 'old-job', state: 'completed', batches: [] } } })
    await diagnosis
    expect(store.aiJob).toBeNull()
    expect(store.aiDiagnosisBatchId).toBe('new-batch')
  })

  it('preserves edits made while the AI job creation request is pending', async () => {
    const store = useCasesStore()
    let finish!: (value: unknown) => void
    vi.spyOn(apiClient, 'post').mockImplementation(() => new Promise(resolve => { finish = resolve }) as never)
    vi.spyOn(apiClient, 'get')
      .mockResolvedValueOnce({ data: { job: { id: 'job-a', state: 'completed', batches: [{ generated_draft_ids: [VERSION.id] }] } } } as never)
      .mockResolvedValueOnce({ data: { case_version: VERSION } } as never)
    const generating = store.generate(['endpoint-1'], 'env-1', '正常流程')
    store.updateDraft('endpoint-1', { ...VERSION, name: '用户正在编辑的未保存内容' })
    finish({ data: { job: { id: 'job-a', state: 'queued', batches: [] } } })
    await generating
    expect(store.drafts['endpoint-1'].name).toBe('用户正在编辑的未保存内容')
    expect(store.aiJob?.state).toBe('completed')
  })

  it('releases the previous diagnosis busy state when resuming the same AI task', async () => {
    const store = useCasesStore()
    store.aiJob = { id: 'job-a', state: 'running', batches: [], endpoint_ids: [], requested_model: 'qwen', actual_model: 'qwen', fallback_used: false, summary: {} }
    let finish!: (value: unknown) => void
    vi.spyOn(apiClient, 'post').mockImplementation(() => new Promise(resolve => { finish = resolve }) as never)
    vi.spyOn(apiClient, 'get').mockResolvedValue({ data: { job: { id: 'job-a', state: 'completed', batches: [] } } } as never)
    const diagnosis = store.diagnoseAiValidation('batch-a', 0)
    await store.pollAiJob('job-a', { maxAttempts: 1, delayMs: 0 })
    finish({ data: { job: { id: 'job-a', state: 'running', batches: [] } } })
    await diagnosis
    expect(store.aiDiagnosisBatchId).toBe('')
    expect(store.aiJob?.state).toBe('completed')
  })

  it('ignores a delayed restored AI version after manual draft editing starts', async () => {
    let resolveVersion!: (value: { data: { case_version: CaseVersion } }) => void
    const completedJob = {
      id: 'job-completed', state: 'completed', endpoint_ids: ['endpoint-1'],
      requested_model: 'qwen', actual_model: 'qwen', fallback_used: false, summary: {},
      batches: [{
        id: 'batch-1', sequence: 1, state: 'completed', endpoint_ids: ['endpoint-1'],
        requested_model: 'qwen', actual_model: 'qwen', fallback_used: false, fallback_reason: '',
        generated_draft_ids: [VERSION.id], validation_errors: [],
      }],
    }
    const client = {
      get: vi.fn()
        .mockResolvedValueOnce({ data: { job: completedJob } })
        .mockImplementationOnce(() => new Promise<{ data: { case_version: CaseVersion } }>(resolve => { resolveVersion = resolve })),
    } as unknown as Pick<ApiClient, 'get'>
    const store = useCasesStore()

    const restore = store.restoreLatestAiJob('project-1', 'source-1', client)
    await vi.waitFor(() => expect(client.get).toHaveBeenCalledTimes(2))
    store.startManualDraft({ id: 'endpoint-1', method: 'GET', path: '/favorite/list', summary: '收藏列表', tags: [] } as ApiEndpoint)
    store.drafts['endpoint-1'].name = '用户正在编辑的草稿'
    resolveVersion({ data: { case_version: VERSION } })
    await restore

    expect(store.versions[VERSION.id]).toBeUndefined()
    expect(store.activeVersionByEndpoint['endpoint-1']).toBeUndefined()
    expect(store.drafts['endpoint-1'].name).toBe('用户正在编辑的草稿')
  })

  it('restores the latest completed AI job and its generated results after reload', async () => {
    const completedJob = {
      id: 'job-completed', state: 'completed', endpoint_ids: ['endpoint-1'],
      requested_model: 'qwen', actual_model: 'qwen', fallback_used: false, summary: {},
      batches: [{ id: 'batch-1', sequence: 1, state: 'completed', endpoint_ids: ['endpoint-1'], requested_model: 'qwen', actual_model: 'qwen', fallback_used: false, fallback_reason: '', generated_draft_ids: ['version-1'], validation_errors: [] }],
    }
    const get = vi.spyOn(apiClient, 'get')
      .mockResolvedValueOnce({ data: { job: completedJob } })
      .mockResolvedValueOnce({ data: { case_version: VERSION } })
    const store = useCasesStore()

    await store.restoreLatestAiJob('project-1', 'source-1')

    expect(store.aiJob?.id).toBe('job-completed')
    expect(store.lastAiJobId).toBe('job-completed')
    expect(store.aiCanResume).toBe(false)
    expect(store.versions['version-1']).toEqual(VERSION)
    expect(get.mock.calls[0][0]).toBe('/api/api-testing/v1/ai-jobs/latest?project_id=project-1&source_revision_id=source-1')
    expect(get.mock.calls[1][0]).toBe('/api/api-testing/v1/case-versions/version-1')
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

  it('loads the requested debug case evidence after the terminal summary arrives', async () => {
    const summaryResult = {
      execution_case_id: 'execution-case-1', case_version_id: 'version-1', endpoint_id: 'endpoint-1',
      execution_role: 'requested', status: 'PASSED', failure_category: '', duration_ms: 21,
      sanitized_result: {}, evidence_loaded: false,
    }
    const get = vi.spyOn(apiClient, 'get')
      .mockResolvedValueOnce({ data: { execution: {
        id: 'execution-1', state: 'DONE', case_statuses: ['PASSED'], summary: { total: 1, passed: 1 },
        case_results: [summaryResult],
      } } })
      .mockResolvedValueOnce({ data: { case_result: {
        ...summaryResult, evidence_loaded: true,
        sanitized_result: { sanitized_response: { status_code: 200 }, assertion_results: [{ passed: true }] },
      } } })
    const store = useCasesStore()

    await store.pollExecution('execution-1', { maxAttempts: 1, delayMs: 0 })

    expect(get.mock.calls[1]?.[0]).toBe('/api/api-testing/v1/executions/execution-1/cases/execution-case-1')
    expect(store.debugResult?.executionCaseId).toBe('execution-case-1')
    expect(store.debugResult?.status).toBe('PASSED')
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
