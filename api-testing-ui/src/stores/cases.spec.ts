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

  it('prevents a second debug request while the first submission is pending', async () => {
    const store = useCasesStore(); let finish!: (value: unknown) => void
    const post = vi.spyOn(apiClient, 'post').mockImplementationOnce(() => new Promise(resolve => { finish = resolve }) as never)
    const input = { projectId: 'p', sourceRevisionId: 's', environmentRevisionId: 'e', caseVersionId: 'v' }
    const first = store.debug(input)
    expect(store.debugPolling).toBe(true)
    await expect(store.debug(input)).rejects.toThrow('调试')
    expect(post).toHaveBeenCalledTimes(1)
    store.clearDebug(); finish({ data: { execution: { id: 'old', state: 'RUNNING', case_results: [] } } }); await first
  })

  it('does not attach or poll a debug execution after the scope is cleared while POST is pending', async () => {
    const store = useCasesStore()
    let finish!: (value: unknown) => void
    vi.spyOn(apiClient, 'post').mockImplementationOnce(() => new Promise(resolve => { finish = resolve }) as never)
    const get = vi.spyOn(apiClient, 'get')
    const pending = store.debug({ projectId: 'p', sourceRevisionId: 's', environmentRevisionId: 'e', caseVersionId: 'v' })
    store.clearDebug()
    finish({ data: { execution: { id: 'old', state: 'RUNNING', case_results: [] } } })
    await pending
    expect(store.debugExecution).toBeNull()
    expect(store.debugResult).toBeNull()
    expect(get).not.toHaveBeenCalled()
  })

  it('does not attach or poll a submitted debug after its scope guard expires', async () => {
    const store = useCasesStore()
    let finish!: (value: unknown) => void
    let current = true
    vi.spyOn(apiClient, 'post').mockImplementationOnce(() => new Promise(resolve => { finish = resolve }) as never)
    const get = vi.spyOn(apiClient, 'get')
    const pending = store.debug({ projectId: 'p', sourceRevisionId: 's', environmentRevisionId: 'e', caseVersionId: 'v', isCurrent: () => current })
    current = false
    finish({ data: { execution: { id: 'old', state: 'RUNNING', case_results: [] } } })
    await pending
    expect(store.debugExecution).toBeNull()
    expect(get).not.toHaveBeenCalled()
  })

  it('does not attach late result evidence after the debug scope is cleared', async () => {
    const store = useCasesStore()
    let finishEvidence!: (value: unknown) => void
    vi.spyOn(apiClient, 'get').mockResolvedValueOnce({ data: { execution: {
      id: 'old', state: 'DONE', case_results: [{ execution_role: 'requested', execution_case_id: 'case-old' }],
    } } }).mockImplementationOnce(() => new Promise(resolve => { finishEvidence = resolve }) as never)
    const pending = store.pollExecution('old', { maxAttempts: 1, delayMs: 0 })
    await Promise.resolve(); await Promise.resolve()
    store.clearDebug()
    finishEvidence({ data: { case_result: { execution_case_id: 'case-old' } } })
    await pending
    expect(store.debugExecution).toBeNull()
    expect(store.debugResult).toBeNull()
    expect(store.debugError).toBe('')
    expect(store.debugCanResume).toBe(false)
  })

  it('does not show a stale polling failure after clearing the debug scope', async () => {
    const store = useCasesStore()
    let fail!: (error: Error) => void
    vi.spyOn(apiClient, 'get').mockImplementationOnce(() => new Promise((_, reject) => { fail = reject }) as never)
    const pending = store.pollExecution('old', { maxAttempts: 1, delayMs: 0 })
    store.clearDebug(); fail(new Error('old error')); await pending
    expect(store.debugError).toBe('')
    expect(store.debugCanResume).toBe(false)
  })

  it('clears stale server validation feedback when the draft changes', () => {
    const store = useCasesStore()
    store.validationErrors = { 'request.query.pageNum': '查询参数 pageNum 的类型与接口定义不一致' }
    store.validationWarnings = { 'request.query.optional': '可选查询参数未填写' }

    store.updateDraft('endpoint-1', { ...VERSION, request: { ...VERSION.request, query: { pageNum: 1 } } })

    expect(store.validationErrors).toEqual({})
    expect(store.validationWarnings).toEqual({})
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

  it('preserves the response warning threshold when loading and resaving a case', async () => {
    const persisted = { ...VERSION, processing: { pre: [], post: [], response_time_warning_ms: 1500 } }
    vi.spyOn(apiClient, 'get').mockResolvedValue({ data: { case_version: persisted } })
    const post = vi.spyOn(apiClient, 'post').mockResolvedValue({ data: { case_version: { ...persisted, version: 2 } } })
    const store = useCasesStore()
    await store.loadVersion(VERSION.id)
    expect(store.drafts[VERSION.endpoint_id].processing.response_time_warning_ms).toBe(1500)
    await store.save(VERSION.endpoint_id)
    expect((post.mock.calls[0][1] as { case: { processing: { response_time_warning_ms: number } } }).case.processing.response_time_warning_ms).toBe(1500)
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

  it('does not prepare the old version if selection changes during post-save validation', async () => {
    const store = useCasesStore(); store.registerVersion(VERSION)
    const other = { ...VERSION, id: 'other-version', case_id: 'other-case', name: 'other case' }
    store.registerVersion(other, false)
    store.updateDraft(VERSION.endpoint_id, { ...JSON.parse(JSON.stringify(store.drafts[VERSION.endpoint_id])), assertions: [{ type: 'status_code', operator: 'equals', expected: 200, timeout_ms: 0, enabled: true }] })
    const saved = { ...VERSION, id: 'version-2', version: 2 }
    vi.spyOn(apiClient, 'post').mockResolvedValueOnce({ data: { case_version: saved } })
    let finishValidation!: () => void
    vi.spyOn(store, 'validate').mockImplementationOnce(() => new Promise(resolve => { finishValidation = resolve }))
    const pending = store.saveForDebug(VERSION.endpoint_id, 'environment-1')
    await Promise.resolve(); await Promise.resolve()
    expect(store.activeVersionByEndpoint[VERSION.endpoint_id]).toBe('version-2')
    store.setActiveVersion(VERSION.endpoint_id, 'other-version')
    finishValidation()
    await expect(pending).rejects.toThrow('调试未开始')
    expect(store.activeVersionByEndpoint[VERSION.endpoint_id]).toBe('other-version')
  })

  it('does not prepare debugging when the user edits while the draft is being saved', async () => {
    const store = useCasesStore(); store.registerVersion(VERSION)
    store.updateDraft(VERSION.endpoint_id, { ...JSON.parse(JSON.stringify(store.drafts[VERSION.endpoint_id])), assertions: [{ type: 'status_code', operator: 'equals', expected: 200, timeout_ms: 0, enabled: true }] })
    let finish!: (value: unknown) => void
    vi.spyOn(apiClient, 'post').mockImplementationOnce(() => new Promise(resolve => { finish = resolve }) as never)
    vi.spyOn(store, 'validate').mockResolvedValue()
    const pending = store.saveForDebug(VERSION.endpoint_id, 'environment-1')
    store.updateDraft(VERSION.endpoint_id, { ...JSON.parse(JSON.stringify(store.drafts[VERSION.endpoint_id])), name: 'newer edit' })
    finish({ data: { case_version: { ...VERSION, id: 'version-2', version: 2 } } })
    await expect(pending).rejects.toThrow('调试未开始')
    expect(store.drafts[VERSION.endpoint_id].name).toBe('newer edit')
    expect(store.activeVersionByEndpoint[VERSION.endpoint_id]).toBe('version-2')
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

  it('does not replace the latest source cases with an older response', async () => {
    let resolveOld!: (value: unknown) => void
    const newVersion = { ...VERSION, id: 'new-version', case_id: 'new-case' }
    const get = vi.fn().mockImplementationOnce(() => new Promise(resolve => { resolveOld = resolve }))
      .mockResolvedValueOnce({ data: { case_versions: [newVersion] } })
    const store = useCasesStore()
    const old = store.loadSavedCases('old', { get } as unknown as Pick<ApiClient, 'get'>)
    await store.loadSavedCases('new', { get } as unknown as Pick<ApiClient, 'get'>)
    resolveOld({ data: { case_versions: [VERSION] } })
    expect(await old).toBe(false)
    expect(Object.keys(store.versions)).toEqual(['new-version'])
  })

  it('keeps dirty content and the original version after moving its group', async () => {
    const store = useCasesStore(); store.registerVersion(VERSION)
    store.updateDraft(VERSION.endpoint_id, { ...JSON.parse(JSON.stringify(store.drafts[VERSION.endpoint_id])), name: 'unsaved' })
    vi.spyOn(apiClient, 'put').mockResolvedValue({ data: { case_version: { ...VERSION, group_name: 'group' } } })
    await store.updateVersionGroup(VERSION.id, 'group')
    expect(store.drafts[VERSION.endpoint_id].name).toBe('unsaved')
    expect(store.activeVersionByEndpoint[VERSION.endpoint_id]).toBe(VERSION.id)
  })

  it('keeps dirty content while generated saved cases arrive', async () => {
    const store = useCasesStore(); store.registerVersion(VERSION)
    store.updateDraft(VERSION.endpoint_id, { ...JSON.parse(JSON.stringify(store.drafts[VERSION.endpoint_id])), name: 'unsaved' })
    const generated = { ...VERSION, id: 'generated', case_id: 'other', name: 'generated' }
    vi.spyOn(apiClient, 'post').mockResolvedValue({ data: { case_versions: [generated] } })
    await store.generateBasicPositive([VERSION.endpoint_id], 'environment-a')
    expect(store.drafts[VERSION.endpoint_id].name).toBe('unsaved')
    expect(store.activeVersionByEndpoint[VERSION.endpoint_id]).toBe(VERSION.id)
  })

  it('retains an active edited preview when generating another candidate for the same endpoint', async () => {
    const store = useCasesStore()
    store.generatedPreviews = [{ id: 'old-preview', endpoint_id: VERSION.endpoint_id, case: VERSION } as unknown as GeneratedCasePreview]
    store.setDraftFromGeneratedPreview('old-preview')
    store.updateDraft(VERSION.endpoint_id, { ...JSON.parse(JSON.stringify(store.drafts[VERSION.endpoint_id])), name: 'unsaved candidate' })
    const newer = { id: 'new-preview', endpoint_id: VERSION.endpoint_id, case: VERSION }
    vi.spyOn(apiClient, 'post').mockResolvedValue({ data: { case_previews: [newer] } })
    await store.previewBasicPositive([VERSION.endpoint_id], 'environment-a')
    expect(store.generatedPreviews.map(item => item.id)).toEqual(['old-preview', 'new-preview'])
    expect(store.activeGeneratedPreviewId).toBe('old-preview')
    expect(store.drafts[VERSION.endpoint_id].name).toBe('unsaved candidate')
  })

  it('can load a generated version after discarding an edited preview', async () => {
    const store = useCasesStore()
    store.generatedPreviews = [{ id: 'preview-a', endpoint_id: VERSION.endpoint_id, case: VERSION } as unknown as GeneratedCasePreview]
    store.setDraftFromGeneratedPreview('preview-a')
    store.discardGeneratedPreview('preview-a')
    const client = { get: vi.fn().mockResolvedValue({ data: { case_version: VERSION } }) } as unknown as Pick<ApiClient, 'get'>
    await store.loadVersion(VERSION.id, client)
    expect(store.activeVersionByEndpoint[VERSION.endpoint_id]).toBe(VERSION.id)
  })

  it('does not bind a dirty existing case to a separately saved preview', async () => {
    const store = useCasesStore(); store.registerVersion(VERSION)
    store.updateDraft(VERSION.endpoint_id, { ...JSON.parse(JSON.stringify(store.drafts[VERSION.endpoint_id])), name: 'edit A' })
    store.generatedPreviews = [{ id: 'preview-b', endpoint_id: VERSION.endpoint_id, case: VERSION } as unknown as GeneratedCasePreview]
    const b = { ...VERSION, id: 'version-b', case_id: 'case-b', name: 'B' }
    const v2 = { ...VERSION, id: 'version-2', version: 2, name: 'edit A' }
    const post = vi.spyOn(apiClient, 'post').mockResolvedValueOnce({ data: { case_version: b } }).mockResolvedValueOnce({ data: { case_version: v2 } })
    vi.spyOn(store, 'validate').mockResolvedValue()
    await store.saveGeneratedPreview('preview-b')
    expect(store.drafts[VERSION.endpoint_id].name).toBe('edit A')
    expect(store.activeVersionByEndpoint[VERSION.endpoint_id]).toBe(VERSION.id)
    await store.save(VERSION.endpoint_id)
    expect(post.mock.calls[1][0]).toBe('/api/api-testing/v1/cases/case-1/versions')
  })

  it('keeps newer edits made while saving a generated preview', async () => {
    const store = useCasesStore()
    store.generatedPreviews = [{ id: 'preview-a', endpoint_id: VERSION.endpoint_id, case: VERSION } as unknown as GeneratedCasePreview]
    store.setDraftFromGeneratedPreview('preview-a')
    const sent = JSON.parse(JSON.stringify(store.drafts[VERSION.endpoint_id]))
    let finish!: (value: unknown) => void
    vi.spyOn(apiClient, 'post').mockImplementationOnce(() => new Promise(resolve => { finish = resolve }) as never)
    const pending = store.saveGeneratedPreview('preview-a', sent)
    store.updateDraft(VERSION.endpoint_id, { ...JSON.parse(JSON.stringify(store.drafts[VERSION.endpoint_id])), name: 'newer edit' })
    finish({ data: { case_version: VERSION } }); await pending
    expect(store.drafts[VERSION.endpoint_id].name).toBe('newer edit')
    expect(store.activeVersionByEndpoint[VERSION.endpoint_id]).toBe(VERSION.id)
  })

  it('does not overwrite a new manual selection while an earlier preview save returns', async () => {
    const store = useCasesStore()
    store.generatedPreviews = [{ id: 'preview-a', endpoint_id: VERSION.endpoint_id, case: VERSION } as unknown as GeneratedCasePreview]
    store.setDraftFromGeneratedPreview('preview-a')
    let finish!: (value: unknown) => void
    vi.spyOn(apiClient, 'post').mockImplementationOnce(() => new Promise(resolve => { finish = resolve }) as never)
    const pending = store.saveGeneratedPreview('preview-a')
    store.startManualDraft({ id: VERSION.endpoint_id, method: 'GET', path: '/favorite/list', summary: 'other', tags: [] } as ApiEndpoint)
    store.updateDraft(VERSION.endpoint_id, { ...JSON.parse(JSON.stringify(store.drafts[VERSION.endpoint_id])), name: 'other manual' })
    finish({ data: { case_version: VERSION } })
    await expect(pending).rejects.toThrow('已保存')
    expect(store.drafts[VERSION.endpoint_id].name).toBe('other manual')
    expect(store.activeVersionByEndpoint[VERSION.endpoint_id]).toBeUndefined()
  })

  it('does not write an old validation result into the current source', async () => {
    const store = useCasesStore()
    const ready = { get: vi.fn().mockResolvedValue({ data: { case_versions: [] } }) } as unknown as Pick<ApiClient, 'get'>
    await store.loadSavedCases('source-a', ready)
    let finish!: (value: unknown) => void
    vi.spyOn(apiClient, 'post').mockImplementationOnce(() => new Promise(resolve => { finish = resolve }) as never)
    const pending = store.validate('version-a')
    await store.loadSavedCases('source-b', ready)
    finish({ data: { validation: { errors: [{ field: 'name', message: 'old error' }], warnings: [] } } })
    await expect(pending).rejects.toThrow('范围已切换')
    expect(store.validationErrors).toEqual({})
  })

  it('does not register a completed group change in a different source', async () => {
    const store = useCasesStore()
    const ready = { get: vi.fn().mockResolvedValue({ data: { case_versions: [VERSION] } }) } as unknown as Pick<ApiClient, 'get'>
    await store.loadSavedCases('source-a', ready)
    let finish!: (value: unknown) => void
    vi.spyOn(apiClient, 'put').mockImplementationOnce(() => new Promise(resolve => { finish = resolve }) as never)
    const pending = store.updateVersionGroup(VERSION.id, 'new-group')
    const empty = { get: vi.fn().mockResolvedValue({ data: { case_versions: [] } }) } as unknown as Pick<ApiClient, 'get'>
    await store.loadSavedCases('source-b', empty)
    finish({ data: { case_version: { ...VERSION, group_name: 'new-group' } } })
    await expect(pending).rejects.toThrow('已')
    expect(store.versions).toEqual({})
  })

  it('keeps a completed preview save out of the new source and does not offer it twice', async () => {
    const store = useCasesStore()
    const ready = { get: vi.fn().mockResolvedValue({ data: { case_versions: [] } }) } as unknown as Pick<ApiClient, 'get'>
    await store.loadSavedCases('source-a', ready)
    store.generatedPreviews = [{ id: 'preview-a', endpoint_id: VERSION.endpoint_id, case: VERSION } as unknown as GeneratedCasePreview]
    store.setDraftFromGeneratedPreview('preview-a')
    let finish!: (value: unknown) => void
    vi.spyOn(apiClient, 'post').mockImplementationOnce(() => new Promise(resolve => { finish = resolve }) as never)
    const pending = store.saveGeneratedPreview('preview-a')
    await store.loadSavedCases('source-b', ready)
    finish({ data: { case_version: VERSION } })
    await expect(pending).rejects.toThrow('已保存')
    expect(store.versions).toEqual({})
    const saved = { get: vi.fn().mockResolvedValue({ data: { case_versions: [VERSION] } }) } as unknown as Pick<ApiClient, 'get'>
    await store.loadSavedCases('source-a', saved)
    expect(store.generatedPreviews).toEqual([])
    expect(store.activeVersionByEndpoint[VERSION.endpoint_id]).toBe(VERSION.id)
  })

  it('does not expose a stale generated preview after switching source', async () => {
    const store = useCasesStore()
    const ready = { get: vi.fn().mockResolvedValue({ data: { case_versions: [] } }) } as unknown as Pick<ApiClient, 'get'>
    await store.loadSavedCases('source-a', ready)
    let finish!: (value: unknown) => void
    vi.spyOn(apiClient, 'post').mockImplementationOnce(() => new Promise(resolve => { finish = resolve }) as never)
    const pending = store.previewBasicPositive(['endpoint-1'], 'environment-a')
    await store.loadSavedCases('source-b', ready)
    finish({ data: { case_previews: [{ id: 'preview-old', endpoint_id: 'endpoint-1', case: {} }] } })
    await expect(pending).rejects.toThrow('范围已切换')
    expect(store.generatedPreviews).toEqual([])
  })

  it('does not register persisted generated cases in a newly selected source', async () => {
    const store = useCasesStore()
    const ready = { get: vi.fn().mockResolvedValue({ data: { case_versions: [] } }) } as unknown as Pick<ApiClient, 'get'>
    await store.loadSavedCases('source-a', ready)
    let finish!: (value: unknown) => void
    vi.spyOn(apiClient, 'post').mockImplementationOnce(() => new Promise(resolve => { finish = resolve }) as never)
    const pending = store.generateBasicPositive(['endpoint-1'], 'environment-a')
    await store.loadSavedCases('source-b', ready)
    finish({ data: { case_versions: [VERSION] } })
    await expect(pending).rejects.toThrow('已')
    expect(store.versions).toEqual({})
  })

  it('does not let an old archive response delete a new-source draft', async () => {
    const store = useCasesStore()
    const readyA = { get: vi.fn().mockResolvedValue({ data: { case_versions: [VERSION] } }) } as unknown as Pick<ApiClient, 'get'>
    await store.loadSavedCases('source-a', readyA)
    let finish!: (value: unknown) => void
    vi.spyOn(apiClient, 'delete').mockImplementationOnce(() => new Promise(resolve => { finish = resolve }) as never)
    const pending = store.archiveCase(VERSION.endpoint_id, VERSION.id)
    const b = { ...VERSION, id: 'version-b', case_id: VERSION.case_id, name: 'B' }
    const readyB = { get: vi.fn().mockResolvedValue({ data: { case_versions: [b] } }) } as unknown as Pick<ApiClient, 'get'>
    await store.loadSavedCases('source-b', readyB)
    finish({ data: {} })
    await expect(pending).rejects.toThrow('已')
    expect(store.versions['version-b']).toBeDefined()
    expect(store.drafts[VERSION.endpoint_id].name).toBe('B')
  })

  it('retains edits made while a saved case request is in flight', async () => {
    const store = useCasesStore()
    const ready = { get: vi.fn().mockResolvedValue({ data: { case_versions: [VERSION] } }) } as unknown as Pick<ApiClient, 'get'>
    await store.loadSavedCases('source-a', ready)
    store.updateDraft(VERSION.endpoint_id, { ...JSON.parse(JSON.stringify(store.drafts[VERSION.endpoint_id])), name: 'sent for save' })
    let finish!: (value: unknown) => void
    vi.spyOn(apiClient, 'post').mockImplementationOnce(() => new Promise(resolve => { finish = resolve }) as never)
    vi.spyOn(store, 'validate').mockResolvedValue()
    const pending = store.save(VERSION.endpoint_id)
    store.updateDraft(VERSION.endpoint_id, { ...JSON.parse(JSON.stringify(store.drafts[VERSION.endpoint_id])), name: 'edited after save started' })
    const v2 = { ...VERSION, id: 'version-2', version: 2, name: 'sent for save' }
    finish({ data: { case_version: v2 } }); await pending
    expect(store.drafts[VERSION.endpoint_id].name).toBe('edited after save started')
    expect(store.activeVersionByEndpoint[VERSION.endpoint_id]).toBe('version-2')
  })

  it('does not bind a newer manual draft to an earlier manual save', async () => {
    const store = useCasesStore()
    const endpoint = { id: VERSION.endpoint_id, method: 'GET', path: '/favorite/list', summary: 'manual A', tags: [] } as ApiEndpoint
    store.startManualDraft(endpoint)
    let finish!: (value: unknown) => void
    const b = { ...VERSION, id: 'version-b', case_id: 'case-b', name: 'manual B' }
    const post = vi.spyOn(apiClient, 'post').mockImplementationOnce(() => new Promise(resolve => { finish = resolve }) as never)
      .mockResolvedValueOnce({ data: { case_version: b } })
    vi.spyOn(store, 'validate').mockResolvedValue()
    const first = store.save(endpoint.id)
    store.startManualDraft({ ...endpoint, summary: 'manual B' })
    store.updateDraft(endpoint.id, { ...JSON.parse(JSON.stringify(store.drafts[endpoint.id])), name: 'manual B' })
    finish({ data: { case_version: VERSION } })
    await expect(first).rejects.toThrow('已保存')
    expect(store.drafts[endpoint.id].name).toBe('manual B')
    expect(store.activeVersionByEndpoint[endpoint.id]).toBeUndefined()
    await store.save(endpoint.id)
    expect(post.mock.calls[1][0]).toBe('/api/api-testing/v1/cases')
  })

  it('does not bind a new manual draft after an earlier save returns across source switches', async () => {
    const store = useCasesStore()
    const endpoint = { id: VERSION.endpoint_id, method: 'GET', path: '/favorite/list', summary: 'A', tags: [] } as ApiEndpoint
    const empty = { get: vi.fn().mockResolvedValue({ data: { case_versions: [] } }) } as unknown as Pick<ApiClient, 'get'>
    await store.loadSavedCases('source-a', empty); store.startManualDraft(endpoint)
    let finish!: (value: unknown) => void
    const post = vi.spyOn(apiClient, 'post').mockImplementationOnce(() => new Promise(resolve => { finish = resolve }) as never)
      .mockResolvedValueOnce({ data: { case_version: { ...VERSION, id: 'version-b', case_id: 'case-b' } } })
    vi.spyOn(store, 'validate').mockResolvedValue()
    const pending = store.save(endpoint.id)
    await store.loadSavedCases('source-b', empty)
    await store.loadSavedCases('source-a', empty)
    store.startManualDraft({ ...endpoint, summary: 'B' })
    store.updateDraft(endpoint.id, { ...JSON.parse(JSON.stringify(store.drafts[endpoint.id])), name: 'new B' })
    await store.loadSavedCases('source-b', empty)
    finish({ data: { case_version: VERSION } }); await expect(pending).rejects.toThrow('已保存')
    const loadedA = { get: vi.fn().mockResolvedValue({ data: { case_versions: [VERSION] } }) } as unknown as Pick<ApiClient, 'get'>
    await store.loadSavedCases('source-a', loadedA)
    expect(store.drafts[endpoint.id].name).toBe('new B')
    expect(store.activeVersionByEndpoint[endpoint.id]).toBeUndefined()
    await store.save(endpoint.id)
    expect(post.mock.calls[1][0]).toBe('/api/api-testing/v1/cases')
  })

  it('does not send a second save while the first is pending across source switches', async () => {
    const store = useCasesStore()
    const empty = { get: vi.fn().mockResolvedValue({ data: { case_versions: [] } }) } as unknown as Pick<ApiClient, 'get'>
    await store.loadSavedCases('source-a', empty)
    store.startManualDraft({ id: VERSION.endpoint_id, method: 'GET', path: '/favorite/list', summary: 'new', tags: [] } as ApiEndpoint)
    let finish!: (value: unknown) => void
    const post = vi.spyOn(apiClient, 'post').mockImplementationOnce(() => new Promise(resolve => { finish = resolve }) as never)
    const first = store.save(VERSION.endpoint_id)
    await store.loadSavedCases('source-b', empty)
    await store.loadSavedCases('source-a', empty)
    await expect(store.save(VERSION.endpoint_id)).rejects.toThrow('保存')
    expect(post).toHaveBeenCalledTimes(1)
    finish({ data: { case_version: VERSION } })
    await expect(first).rejects.toThrow('已保存')
  })

  it('does not publish an old save while the original source is awaiting reauthorization', async () => {
    const store = useCasesStore()
    const empty = { get: vi.fn().mockResolvedValue({ data: { case_versions: [] } }) } as unknown as Pick<ApiClient, 'get'>
    await store.loadSavedCases('source-a', empty)
    store.startManualDraft({ id: VERSION.endpoint_id, method: 'GET', path: '/favorite/list', summary: 'new', tags: [] } as ApiEndpoint)
    let finishSave!: (value: unknown) => void
    vi.spyOn(apiClient, 'post').mockImplementationOnce(() => new Promise(resolve => { finishSave = resolve }) as never)
    const saving = store.save(VERSION.endpoint_id)
    await store.loadSavedCases('source-b', empty)
    let finishReload!: (value: unknown) => void
    const delayed = { get: vi.fn(() => new Promise(resolve => { finishReload = resolve })) } as unknown as Pick<ApiClient, 'get'>
    const loading = store.loadSavedCases('source-a', delayed)
    finishSave({ data: { case_version: VERSION } })
    await expect(saving).rejects.toThrow('已保存')
    expect(store.versions).toEqual({})
    expect(store.drafts).toEqual({})
    finishReload({ data: { case_versions: [VERSION] } }); await loading
    expect(store.activeVersionByEndpoint[VERSION.endpoint_id]).toBe(VERSION.id)
  })

  it('does not put a completed save into the new source after switching', async () => {
    const store = useCasesStore()
    const readyA = { get: vi.fn().mockResolvedValue({ data: { case_versions: [VERSION] } }) } as unknown as Pick<ApiClient, 'get'>
    await store.loadSavedCases('source-a', readyA)
    store.updateDraft(VERSION.endpoint_id, { ...JSON.parse(JSON.stringify(store.drafts[VERSION.endpoint_id])), name: 'saved A' })
    let finish!: (value: unknown) => void
    vi.spyOn(apiClient, 'post').mockImplementationOnce(() => new Promise(resolve => { finish = resolve }) as never)
    vi.spyOn(store, 'validate').mockResolvedValue()
    const pending = store.save(VERSION.endpoint_id)
    const b = { ...VERSION, id: 'version-b', case_id: 'case-b', name: 'B' }
    const readyB = { get: vi.fn().mockResolvedValue({ data: { case_versions: [b] } }) } as unknown as Pick<ApiClient, 'get'>
    await store.loadSavedCases('source-b', readyB)
    const v2 = { ...VERSION, id: 'version-2', version: 2, name: 'saved A' }
    finish({ data: { case_version: v2 } })
    await expect(pending).rejects.toThrow('已保存')
    expect(Object.keys(store.versions)).toEqual(['version-b'])
    expect(store.drafts[VERSION.endpoint_id].name).toBe('B')
    const reloadedA = { get: vi.fn().mockResolvedValue({ data: { case_versions: [v2] } }) } as unknown as Pick<ApiClient, 'get'>
    await store.loadSavedCases('source-a', reloadedA)
    expect(store.activeVersionByEndpoint[VERSION.endpoint_id]).toBe('version-2')
    expect(store.drafts[VERSION.endpoint_id].name).toBe('saved A')
  })

  it('does not let a late old-source version response contaminate the new source', async () => {
    const store = useCasesStore()
    const ready = { get: vi.fn().mockResolvedValue({ data: { case_versions: [VERSION] } }) } as unknown as Pick<ApiClient, 'get'>
    await store.loadSavedCases('source-a', ready)
    let finish!: (value: unknown) => void
    const delayed = { get: vi.fn(() => new Promise(resolve => { finish = resolve })) } as unknown as Pick<ApiClient, 'get'>
    const old = store.loadVersion('version-1', delayed)
    const b = { ...VERSION, id: 'version-b', case_id: 'case-b', name: 'B' }
    const clientB = { get: vi.fn().mockResolvedValue({ data: { case_versions: [b] } }) } as unknown as Pick<ApiClient, 'get'>
    await store.loadSavedCases('source-b', clientB)
    finish({ data: { case_version: VERSION } }); await old
    expect(Object.keys(store.versions)).toEqual(['version-b'])
    expect(store.drafts[VERSION.endpoint_id].name).toBe('B')
  })

  it('does not overwrite a dirty manual draft while loading an AI version for the same endpoint', async () => {
    const store = useCasesStore()
    const ready = { get: vi.fn().mockResolvedValue({ data: { case_versions: [VERSION] } }) } as unknown as Pick<ApiClient, 'get'>
    await store.loadSavedCases('source-a', ready)
    store.startManualDraft({ id: VERSION.endpoint_id, method: 'GET', path: '/favorite/list', summary: 'new', tags: [] } as ApiEndpoint)
    store.updateDraft(VERSION.endpoint_id, { ...JSON.parse(JSON.stringify(store.drafts[VERSION.endpoint_id])), name: 'manual work' })
    const ai = { ...VERSION, id: 'ai-version', case_id: 'ai-case', name: 'AI suggestion' }
    const client = { get: vi.fn().mockResolvedValue({ data: { case_version: ai } }) } as unknown as Pick<ApiClient, 'get'>
    await store.loadVersion(ai.id, client)
    expect(store.drafts[VERSION.endpoint_id].name).toBe('manual work')
    expect(store.activeVersionByEndpoint[VERSION.endpoint_id]).toBeUndefined()
    expect(store.versions['ai-version']).toBeDefined()
  })

  it('does not accept draft writes after the source read fails', async () => {
    const store = useCasesStore()
    const ready = { get: vi.fn().mockResolvedValue({ data: { case_versions: [VERSION] } }) } as unknown as Pick<ApiClient, 'get'>
    await store.loadSavedCases('source-a', ready)
    const endpoint = { id: VERSION.endpoint_id, method: 'GET', path: '/favorite/list', summary: 'case', tags: [] } as ApiEndpoint
    const denied = { get: vi.fn().mockRejectedValue(new Error('404')) } as unknown as Pick<ApiClient, 'get'>
    await expect(store.loadSavedCases('source-a', denied)).rejects.toThrow('404')
    const transient = store.draftFor(endpoint)
    store.updateDraft(endpoint.id, { ...JSON.parse(JSON.stringify(transient)), name: 'must not store' })
    expect(store.drafts[endpoint.id]).toBeUndefined()
    await expect(store.save(endpoint.id)).rejects.toThrow('重新读取')
  })

  it('hides a previously visible source after a denied reload but preserves edits for reauthorization', async () => {
    const store = useCasesStore()
    const ready = { get: vi.fn().mockResolvedValue({ data: { case_versions: [VERSION] } }) } as unknown as Pick<ApiClient, 'get'>
    await store.loadSavedCases('source-a', ready)
    store.updateDraft(VERSION.endpoint_id, { ...JSON.parse(JSON.stringify(store.drafts[VERSION.endpoint_id])), name: 'unsaved A' })
    const denied = { get: vi.fn().mockRejectedValue(new Error('404')) } as unknown as Pick<ApiClient, 'get'>
    await expect(store.loadSavedCases('source-a', denied)).rejects.toThrow('404')
    expect(store.versions).toEqual({})
    expect(store.drafts).toEqual({})
    await store.loadSavedCases('source-a', ready)
    expect(store.drafts[VERSION.endpoint_id].name).toBe('unsaved A')
  })

  it('hides prior-source drafts immediately during a denied source switch', async () => {
    const store = useCasesStore()
    const ready = { get: vi.fn().mockResolvedValue({ data: { case_versions: [VERSION] } }) } as unknown as Pick<ApiClient, 'get'>
    await store.loadSavedCases('source-a', ready)
    store.updateDraft(VERSION.endpoint_id, { ...JSON.parse(JSON.stringify(store.drafts[VERSION.endpoint_id])), name: 'private A' })
    let fail!: (error: Error) => void
    const denied = { get: vi.fn(() => new Promise((_, reject) => { fail = reject })) } as unknown as Pick<ApiClient, 'get'>
    const pending = store.loadSavedCases('source-b', denied)
    expect(store.versions).toEqual({})
    expect(store.drafts).toEqual({})
    fail(new Error('403'))
    await expect(pending).rejects.toThrow('403')
    expect(store.versions).toEqual({})
    expect(store.drafts).toEqual({})
    await store.loadSavedCases('source-a', ready)
    expect(store.drafts[VERSION.endpoint_id].name).toBe('private A')
  })

  it('refuses to restore source drafts in a browser with no authenticated session', async () => {
    vi.stubGlobal('window', {})
    vi.stubGlobal('sessionStorage', { getItem: () => null })
    try {
      const store = useCasesStore()
      const client = { get: vi.fn().mockResolvedValue({ data: { case_versions: [VERSION] } }) } as unknown as Pick<ApiClient, 'get'>
      await expect(store.loadSavedCases('source-a', client)).rejects.toThrow('登录')
      expect(client.get).not.toHaveBeenCalled()
      expect(store.versions).toEqual({})
    } finally { vi.unstubAllGlobals() }
  })

  it('refuses to save a prior session draft after the active session changes', async () => {
    let token = 'session-a'; vi.stubGlobal('sessionStorage', { getItem: () => token })
    try {
      const store = useCasesStore()
      const client = { get: vi.fn().mockResolvedValue({ data: { case_versions: [VERSION] } }) } as unknown as Pick<ApiClient, 'get'>
      await store.loadSavedCases('source-a', client)
      store.updateDraft(VERSION.endpoint_id, { ...JSON.parse(JSON.stringify(store.drafts[VERSION.endpoint_id])), name: 'private A' })
      token = 'session-b'
      const post = vi.spyOn(apiClient, 'post')
      await expect(store.save(VERSION.endpoint_id)).rejects.toThrow('登录')
      expect(post).not.toHaveBeenCalled()
      expect(store.versions).toEqual({})
      expect(store.drafts).toEqual({})
    } finally { vi.unstubAllGlobals() }
  })

  it('does not restore an earlier session draft after an account change', async () => {
    let token = 'session-a'
    vi.stubGlobal('sessionStorage', { getItem: () => token })
    try {
      const store = useCasesStore()
      const client = { get: vi.fn().mockResolvedValue({ data: { case_versions: [VERSION] } }) } as unknown as Pick<ApiClient, 'get'>
      await store.loadSavedCases('source-a', client)
      store.updateDraft(VERSION.endpoint_id, { ...JSON.parse(JSON.stringify(store.drafts[VERSION.endpoint_id])), name: 'private A' })
      token = 'session-b'
      await store.loadSavedCases('source-a', client)
      expect(store.drafts[VERSION.endpoint_id].name).toBe(VERSION.name)
    } finally { vi.unstubAllGlobals() }
  })

  it('discards a response requested under a previous session', async () => {
    let token = 'session-a'
    vi.stubGlobal('sessionStorage', { getItem: () => token })
    try {
      const store = useCasesStore()
      let finish!: (value: unknown) => void
      const client = { get: vi.fn(() => new Promise(resolve => { finish = resolve })) } as unknown as Pick<ApiClient, 'get'>
      const pending = store.loadSavedCases('source-a', client)
      token = 'session-b'
      finish({ data: { case_versions: [VERSION] } })
      expect(await pending).toBe(false)
      expect(store.versions).toEqual({})
    } finally { vi.unstubAllGlobals() }
  })

  it('binds a newly discovered saved case when the existing blank draft was untouched', async () => {
    const store = useCasesStore()
    const client = { get: vi.fn().mockResolvedValueOnce({ data: { case_versions: [] } }).mockResolvedValueOnce({ data: { case_versions: [VERSION] } }) } as unknown as Pick<ApiClient, 'get'>
    await store.loadSavedCases('source-a', client)
    store.draftFor({ id: VERSION.endpoint_id, method: 'GET', path: '/favorite/list', summary: 'blank', tags: [] } as ApiEndpoint)
    await store.loadSavedCases('source-a', client)
    expect(store.drafts[VERSION.endpoint_id].name).toBe(VERSION.name)
    expect(store.activeVersionByEndpoint[VERSION.endpoint_id]).toBe(VERSION.id)
  })

  it('refreshes untouched draft content from the latest saved version', async () => {
    const store = useCasesStore()
    const refreshed = { ...VERSION, name: 'server latest' }
    const get = vi.fn().mockResolvedValueOnce({ data: { case_versions: [VERSION] } })
      .mockResolvedValueOnce({ data: { case_versions: [refreshed] } })
    const client = { get } as unknown as Pick<ApiClient, 'get'>
    await store.loadSavedCases('source-a', client)
    await store.loadSavedCases('source-a', client)
    expect(store.drafts[VERSION.endpoint_id].name).toBe('server latest')
  })

  it('retains an unsaved edit and original version when reloading the current source', async () => {
    const store = useCasesStore()
    const client = { get: vi.fn().mockResolvedValue({ data: { case_versions: [VERSION] } }) } as unknown as Pick<ApiClient, 'get'>
    await store.loadSavedCases('source-a', client)
    store.updateDraft(VERSION.endpoint_id, { ...JSON.parse(JSON.stringify(store.drafts[VERSION.endpoint_id])), name: 'unsaved edit' })
    await store.loadSavedCases('source-a', client)
    expect(store.drafts[VERSION.endpoint_id].name).toBe('unsaved edit')
    expect(store.activeVersionByEndpoint[VERSION.endpoint_id]).toBe(VERSION.id)
  })

  it('retains edits made while current-source reload is pending', async () => {
    const store = useCasesStore()
    const ready = { get: vi.fn().mockResolvedValue({ data: { case_versions: [VERSION] } }) } as unknown as Pick<ApiClient, 'get'>
    await store.loadSavedCases('source-a', ready)
    let finish!: (value: unknown) => void
    const delayed = { get: vi.fn(() => new Promise(resolve => { finish = resolve })) } as unknown as Pick<ApiClient, 'get'>
    const pending = store.loadSavedCases('source-a', delayed)
    store.updateDraft(VERSION.endpoint_id, { ...JSON.parse(JSON.stringify(store.drafts[VERSION.endpoint_id])), name: 'edit while loading' })
    finish({ data: { case_versions: [VERSION] } }); await pending
    expect(store.drafts[VERSION.endpoint_id].name).toBe('edit while loading')
  })

  it('keeps drafts and active versions isolated by source even for identical endpoint IDs', async () => {
    const store = useCasesStore()
    const versionB = { ...VERSION, id: 'version-b', case_id: 'case-b', project_id: 'project-b', name: 'B saved' }
    const clientA = { get: vi.fn().mockResolvedValue({ data: { case_versions: [VERSION] } }) } as unknown as Pick<ApiClient, 'get'>
    const clientB = { get: vi.fn().mockResolvedValue({ data: { case_versions: [versionB] } }) } as unknown as Pick<ApiClient, 'get'>
    await store.loadSavedCases('source-a', clientA)
    store.updateDraft(VERSION.endpoint_id, { ...JSON.parse(JSON.stringify(store.drafts[VERSION.endpoint_id])), name: 'A unsaved' })
    await store.loadSavedCases('source-b', clientB)
    expect(store.drafts[VERSION.endpoint_id].name).toBe('B saved')
    expect(store.activeVersionByEndpoint[VERSION.endpoint_id]).toBe('version-b')
    store.updateDraft(VERSION.endpoint_id, { ...JSON.parse(JSON.stringify(store.drafts[VERSION.endpoint_id])), name: 'B unsaved' })
    await store.loadSavedCases('source-a', clientA)
    expect(store.drafts[VERSION.endpoint_id].name).toBe('A unsaved')
    expect(store.activeVersionByEndpoint[VERSION.endpoint_id]).toBe(VERSION.id)
    await store.loadSavedCases('source-b', clientB)
    expect(store.drafts[VERSION.endpoint_id].name).toBe('B unsaved')
    expect(store.activeVersionByEndpoint[VERSION.endpoint_id]).toBe('version-b')
  })

  it('does not bind an unsaved manual case to an existing case after same-source reload', async () => {
    const store = useCasesStore()
    const client = { get: vi.fn().mockResolvedValue({ data: { case_versions: [VERSION] } }) } as unknown as Pick<ApiClient, 'get'>
    await store.loadSavedCases('source-a', client)
    store.startManualDraft({ id: VERSION.endpoint_id, method: 'GET', path: '/favorite/list', summary: 'new draft', tags: [] } as ApiEndpoint)
    store.updateDraft(VERSION.endpoint_id, { ...JSON.parse(JSON.stringify(store.drafts[VERSION.endpoint_id])), name: 'new case' })
    await store.loadSavedCases('source-a', client)
    expect(store.drafts[VERSION.endpoint_id].name).toBe('new case')
    expect(store.activeVersionByEndpoint[VERSION.endpoint_id]).toBeUndefined()
  })

  it('preserves an edited draft and active preview when retrying the same source', async () => {
    const store = useCasesStore()
    store.draftSourceRevisionId = 'source-1'; store.draftScopeReady = true
    store.registerVersion(VERSION)
    store.updateDraft(VERSION.endpoint_id, { ...JSON.parse(JSON.stringify(store.drafts[VERSION.endpoint_id])), name: '未保存的修改' })
    const preview = { id: 'preview-1', endpoint_id: VERSION.endpoint_id } as GeneratedCasePreview
    store.generatedPreviews = [preview]
    store.activeGeneratedPreviewId = preview.id
    const client = { get: vi.fn().mockResolvedValue({ data: { case_versions: [VERSION] } }) } as unknown as Pick<ApiClient, 'get'>
    await store.loadSavedCases('source-1', client, { preserveDrafts: true })
    expect(store.drafts[VERSION.endpoint_id].name).toBe('未保存的修改')
    expect(store.generatedPreviews).toEqual([preview])
    expect(store.activeGeneratedPreviewId).toBe(preview.id)
  })

  it('does not bind a new manual draft to a returned existing case on refresh', async () => {
    const store = useCasesStore()
    store.draftSourceRevisionId = 'source-1'; store.draftScopeReady = true
    store.startManualDraft({ id: VERSION.endpoint_id, method: 'GET', path: '/favorite/list', summary: '收藏', tags: [] } as ApiEndpoint)
    const client = { get: vi.fn().mockResolvedValue({ data: { case_versions: [VERSION] } }) } as unknown as Pick<ApiClient, 'get'>
    await store.loadSavedCases('source-1', client, { preserveDrafts: true })
    expect(store.activeVersionByEndpoint[VERSION.endpoint_id]).toBeUndefined()
    const post = vi.spyOn(apiClient, 'post').mockResolvedValue({ data: { case_version: VERSION } })
    vi.spyOn(store, 'validate').mockResolvedValue()
    await store.save(VERSION.endpoint_id)
    expect(post.mock.calls[0][0]).toBe('/api/api-testing/v1/cases')
  })

  it('retains draft and blocks saving when its original case version disappeared', async () => {
    const store = useCasesStore()
    store.draftSourceRevisionId = 'source-1'; store.draftScopeReady = true
    store.registerVersion(VERSION)
    store.updateDraft(VERSION.endpoint_id, { ...JSON.parse(JSON.stringify(store.drafts[VERSION.endpoint_id])), name: '未保存的修改' })
    const other = { ...VERSION, id: 'other', case_id: 'other-case' }
    const client = { get: vi.fn().mockResolvedValue({ data: { case_versions: [other] } }) } as unknown as Pick<ApiClient, 'get'>
    await store.loadSavedCases('source-1', client, { preserveDrafts: true })
    const post = vi.spyOn(apiClient, 'post')
    await expect(store.save(VERSION.endpoint_id)).rejects.toThrow('原用例版本已不在当前列表中')
    expect(post).not.toHaveBeenCalled()
    expect(store.drafts[VERSION.endpoint_id].name).toBe('未保存的修改')
  })

  it('does not bind a draft to an older version when a refresh finishes after save', async () => {
    const store = useCasesStore()
    store.draftSourceRevisionId = 'source-1'; store.draftScopeReady = true
    store.registerVersion(VERSION)
    let finish!: (value: unknown) => void
    const client = { get: vi.fn().mockImplementation(() => new Promise(resolve => { finish = resolve })) } as unknown as Pick<ApiClient, 'get'>
    const pending = store.loadSavedCases('source-1', client, { preserveDrafts: true })
    const saved = { ...VERSION, id: 'new-version', version: 2 }
    vi.spyOn(apiClient, 'post').mockResolvedValue({ data: { case_version: saved } })
    vi.spyOn(store, 'validate').mockResolvedValue()
    await store.save(VERSION.endpoint_id)
    finish({ data: { case_versions: [VERSION] } })
    await pending
    expect(store.activeVersionByEndpoint[VERSION.endpoint_id]).toBe('new-version')
    expect(store.versions['new-version']).toEqual(saved)
  })

  it('does not resurrect a case archived while an older refresh was pending', async () => {
    const store = useCasesStore()
    store.draftSourceRevisionId = 'source-1'; store.draftScopeReady = true
    store.registerVersion(VERSION)
    let finish!: (value: unknown) => void
    const client = { get: vi.fn().mockImplementation(() => new Promise(resolve => { finish = resolve })) } as unknown as Pick<ApiClient, 'get'>
    const pending = store.loadSavedCases('source-1', client, { preserveDrafts: true })
    vi.spyOn(apiClient, 'delete').mockResolvedValue({ data: {} })
    await store.archiveCase(VERSION.endpoint_id, VERSION.id)
    finish({ data: { case_versions: [VERSION] } })
    expect(await pending).toBe(false)
    expect(store.versions[VERSION.id]).toBeUndefined()
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

  it.each([
    ['gateway_timeout', '模型服务或连接等待超时'],
    ['enqueue_failed', '任务未能进入执行队列'],
  ])('shows the saved AI failure category %s after polling', async (errorCode, expected) => {
    vi.spyOn(apiClient, 'get').mockResolvedValue({ data: {
      job: {
        id: 'job-failed', state: 'failed_gateway', endpoint_ids: ['endpoint-1'],
        requested_model: 'qwen', actual_model: '', fallback_used: false, summary: {},
        batches: [{
          id: 'batch-1', sequence: 1, state: 'failed_gateway', endpoint_ids: ['endpoint-1'],
          requested_model: 'qwen', actual_model: '', fallback_used: false, fallback_reason: '',
          generated_draft_ids: [], validation_errors: [], error_code: errorCode,
        }],
      },
    } })
    const store = useCasesStore()

    await store.pollAiJob('job-failed', { maxAttempts: 1, delayMs: 0 })

    expect(store.aiError).toContain(expected)
  })

  it('does not invent a timeout or queue failure for old or mixed AI failures', async () => {
    const batch = {
      id: 'batch-1', sequence: 1, state: 'failed_gateway', endpoint_ids: ['endpoint-1'],
      requested_model: 'qwen', actual_model: '', fallback_used: false, fallback_reason: '',
      generated_draft_ids: [], validation_errors: [],
    }
    const response = { data: { job: {
      id: 'job-failed', state: 'failed_gateway', endpoint_ids: ['endpoint-1'],
      requested_model: 'qwen', actual_model: '', fallback_used: false, summary: {}, batches: [batch],
    } } }
    const get = vi.spyOn(apiClient, 'get').mockResolvedValue(response)
    const store = useCasesStore()
    await store.pollAiJob('job-failed', { maxAttempts: 1, delayMs: 0 })
    expect(store.aiError).toBe('AI 服务调用失败，请稍后重试或检查模型配置')

    get.mockResolvedValue({ data: { job: { ...response.data.job, batches: [
      { ...batch, error_code: 'gateway_timeout' }, { ...batch, id: 'batch-2', error_code: 'enqueue_failed' },
    ] } } })
    await store.pollAiJob('job-failed', { maxAttempts: 1, delayMs: 0 })
    expect(store.aiError).toBe('AI 服务调用失败，请稍后重试或检查模型配置')
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
