import { beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'

import { apiClient } from '../api/client'
import { useBaselinesStore } from './baselines'

describe('baselines store', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    vi.restoreAllMocks()
  })

  it('loads project baselines and exposes selected endpoint ids for task reuse', async () => {
    const get = vi.spyOn(apiClient, 'get').mockResolvedValue({ data: { baselines: [
      {
        id: 'baseline-1', project_id: 'project-1', case_id: 'case-1', case_version_id: 'version-1',
        environment_revision_id: 'environment-1', source_revision_id: 'source-old', endpoint_id: 'endpoint-1', status: 'superseded', case_name: '我的收藏列表',
        case_version: 2, priority: 'P1', origin: 'ai', method: 'POST', path: '/print3d/api/v1/collection/page',
        endpoint_summary: '我的收藏列表', tags: ['家用业务', 'app接口'], adoption_reason: 'passing debug evidence',
        group_name: '我的收藏', adopted_at: '2026-08-12T10:00:00Z',
      },
      {
        id: 'baseline-2', project_id: 'project-1', case_id: 'case-2', case_version_id: 'version-2',
        environment_revision_id: 'environment-2', source_revision_id: 'source-new', endpoint_id: 'endpoint-2', status: 'active', case_name: '取消收藏',
        case_version: 1, priority: 'P1', origin: 'manual', method: 'POST', path: '/print3d/api/v1/collection/cancel',
        endpoint_summary: '取消收藏', tags: ['家用业务', 'app接口'], adoption_reason: 'passing debug evidence',
        group_name: '我的收藏', adopted_at: '2026-08-12T10:01:00Z',
      },
    ] } })
    const store = useBaselinesStore()

    await store.load({
      projectId: 'project-1',
      sourceRevisionId: 'source-1',
      environmentRevisionId: 'environment-1',
    })
    store.toggle('baseline-1')
    store.toggle('baseline-2')

    expect(get).toHaveBeenCalledWith('/api/api-testing/v1/baselines?project_id=project-1')
    expect(store.groups).toEqual(['我的收藏'])
    expect(store.items.map(item => item.source_revision_id)).toEqual(['source-old', 'source-new'])
    expect(store.selectedEndpointIds).toEqual(['endpoint-1', 'endpoint-2'])
  })

  it('loads assertion audit only when requested and clears stale project evidence', async () => {
    const get = vi.spyOn(apiClient, 'get')
      .mockResolvedValueOnce({ data: { baselines: [] } })
      .mockResolvedValueOnce({ data: {
        summary: {
          total: 2,
          verified: 1,
          upgrade_available: 1,
          http_failure: 0,
          business_failure: 0,
          domain_assertion_required: 0,
          evidence_missing: 0,
          needs_review: 1,
          safe_review: 1,
        },
        items: [{
          baseline_id: 'baseline-1',
          case_id: 'case-1',
          case_version_id: 'version-1',
          endpoint_id: 'endpoint-1',
          case_name: '收藏成功',
          method: 'POST',
          path: '/collection/add',
          group_name: '收藏链路',
          environment_revision_id: 'environment-1',
          evidence_execution_case_id: 'execution-case-1',
          evidence_captured_at: '2026-08-27T10:00:00Z',
          status: 'upgrade_available',
          status_label: '可补精确断言',
          reason: '实际响应为业务成功，可在新版本补充精确业务断言后重新调试',
          actual_http_status: 200,
          business_path: '$.code',
          business_value: 0,
          suggested_assertions: [{ type: 'json_path', operator: 'equals', expected: 0, path: '$.code', enabled: true }],
          execution: { level: 'direct', label: '可直接复核', selectable: true, reason: '只读接口，可安全批量复核' },
        }],
      } })
    const store = useBaselinesStore()

    await store.load({ projectId: 'project-1' })
    expect(store.audit).toBeNull()
    expect(get).toHaveBeenCalledTimes(1)

    await store.loadAudit('project-1')
    expect(get).toHaveBeenLastCalledWith('/api/api-testing/v1/baselines/assertion-audit?project_id=project-1')
    expect(store.audit?.summary.safe_review).toBe(1)
    expect(store.auditByBaselineId.get('baseline-1')?.business_value).toBe(0)

    get.mockResolvedValueOnce({ data: { baselines: [] } })
    await store.load({ projectId: 'project-2' })
    expect(store.audit).toBeNull()
  })

  it('keeps baseline data visible when assertion audit fails', async () => {
    const get = vi.spyOn(apiClient, 'get')
      .mockResolvedValueOnce({ data: { baselines: [{
        id: 'baseline-1', project_id: 'project-1', case_id: 'case-1', case_version_id: 'version-1',
        environment_revision_id: 'environment-1', source_revision_id: 'source-old', endpoint_id: 'endpoint-1', status: 'active', case_name: '我的收藏列表',
        case_version: 2, priority: 'P1', origin: 'ai', method: 'POST', path: '/print3d/api/v1/collection/page',
        endpoint_summary: '我的收藏列表', tags: [], group_name: '我的收藏', adoption_reason: '',
        adopted_at: '2026-08-12T10:00:00Z',
      }] } })
      .mockRejectedValueOnce(new Error('请求超时'))
    const store = useBaselinesStore()

    await store.load({ projectId: 'project-1' })
    await store.loadAudit('project-1')

    expect(store.items).toHaveLength(1)
    expect(store.audit).toBeNull()
    expect(store.auditError).toBe('基线断言检查失败：请求超时')
  })

  it('creates a review draft from an exact assertion suggestion and tracks it in the audit row', async () => {
    const post = vi.spyOn(apiClient, 'post').mockResolvedValue({ data: {
      case_version: { id: 'version-review', case_id: 'case-1', version: 3 },
      source_baseline_id: 'baseline-1',
      source_case_version_id: 'version-1',
      suggestion_count: 1,
    } })
    const store = useBaselinesStore()
    store.audit = {
      summary: {
        total: 1, verified: 0, upgrade_available: 1, http_failure: 0, business_failure: 0,
        domain_assertion_required: 0, evidence_missing: 0, needs_review: 1, safe_review: 1,
      },
      items: [{
        baseline_id: 'baseline-1', case_id: 'case-1', case_version_id: 'version-1', endpoint_id: 'endpoint-1',
        case_name: '收藏查询', method: 'GET', path: '/collection/page', group_name: '收藏链路',
        environment_revision_id: 'environment-1', evidence_execution_case_id: 'execution-case-1',
        evidence_captured_at: '2026-08-27T10:00:00Z', status: 'upgrade_available', status_label: '可补精确断言',
        reason: '可补充精确业务断言', actual_http_status: 200, business_path: '$.code', business_value: 0,
        suggested_assertions: [{ type: 'json_path', operator: 'equals', expected: 0, path: '$.code', enabled: true }],
        upgrade_draft_case_version_id: null,
        execution: { level: 'direct', label: '可直接复核', selectable: true, reason: '只读接口' },
      }],
    }

    const version = await store.createAssertionUpgradeDraft('baseline-1')

    expect(post).toHaveBeenCalledWith('/api/api-testing/v1/baselines/baseline-1/assertion-upgrade-draft', {})
    expect(version.id).toBe('version-review')
    expect(store.auditByBaselineId.get('baseline-1')?.upgrade_draft_case_version_id).toBe('version-review')
  })

  it('ignores an audit response invalidated by project navigation', async () => {
    let resolveAudit!: (value: unknown) => void
    vi.spyOn(apiClient, 'get').mockReturnValue(new Promise(resolve => {
      resolveAudit = resolve
    }) as never)
    const store = useBaselinesStore()

    const pending = store.loadAudit('project-1')
    store.clearAudit()
    resolveAudit({ data: {
      summary: {
        total: 0, verified: 0, upgrade_available: 0, http_failure: 0, business_failure: 0,
        domain_assertion_required: 0, evidence_missing: 0, needs_review: 0, safe_review: 0,
      },
      items: [],
    } })
    await pending

    expect(store.audit).toBeNull()
    expect(store.auditProjectId).toBe('')
    expect(store.auditLoading).toBe(false)
  })

  it('renames selected baselines into a platform group', async () => {
    vi.spyOn(apiClient, 'post').mockResolvedValue({ data: { baselines: [
      { id: 'baseline-1', group_name: '发版冒烟' },
      { id: 'baseline-2', group_name: '发版冒烟' },
    ] } })
    const store = useBaselinesStore()
    store.items = [
      {
        id: 'baseline-1', project_id: 'project-1', case_id: 'case-1', case_version_id: 'version-1',
        environment_revision_id: 'environment-1', source_revision_id: 'source-old', endpoint_id: 'endpoint-1', status: 'active', case_name: '我的收藏列表',
        case_version: 2, priority: 'P1', origin: 'ai', method: 'POST', path: '/print3d/api/v1/collection/page',
        endpoint_summary: '我的收藏列表', tags: [], group_name: '未分组', adoption_reason: '',
        adopted_at: '2026-08-12T10:00:00Z',
      },
      {
        id: 'baseline-2', project_id: 'project-1', case_id: 'case-2', case_version_id: 'version-2',
        environment_revision_id: 'environment-2', source_revision_id: 'source-new', endpoint_id: 'endpoint-2', status: 'active', case_name: '取消收藏',
        case_version: 1, priority: 'P1', origin: 'manual', method: 'POST', path: '/print3d/api/v1/collection/cancel',
        endpoint_summary: '取消收藏', tags: [], group_name: '未分组', adoption_reason: '',
        adopted_at: '2026-08-12T10:01:00Z',
      },
    ]
    store.select(['baseline-1', 'baseline-2'])
    store.audit = { summary: {}, items: [] } as never

    await store.updateGroup(store.selectedIds, '发版冒烟')

    expect(apiClient.post).toHaveBeenCalledWith('/api/api-testing/v1/baselines/bulk-group', {
      baseline_ids: ['baseline-1', 'baseline-2'],
      group_name: '发版冒烟',
    })
    expect(store.groups).toEqual(['发版冒烟'])
    expect(store.audit).toBeNull()
  })

  it('archives a baseline without deleting the reusable case draft', async () => {
    vi.spyOn(apiClient, 'delete').mockResolvedValue({ data: { baseline: { id: 'baseline-1', status: 'archived' } } })
    const store = useBaselinesStore()
    store.items = [
      {
        id: 'baseline-1', project_id: 'project-1', case_id: 'case-1', case_version_id: 'version-1',
        environment_revision_id: 'environment-1', source_revision_id: 'source-old', endpoint_id: 'endpoint-1', status: 'superseded', case_name: '我的收藏列表',
        case_version: 2, priority: 'P1', origin: 'ai', method: 'POST', path: '/print3d/api/v1/collection/page',
        endpoint_summary: '我的收藏列表', tags: [], group_name: '我的收藏', adoption_reason: '',
        adopted_at: '2026-08-12T10:00:00Z',
      },
    ]
    store.select(['baseline-1'])

    await store.archive('baseline-1')

    expect(apiClient.delete).toHaveBeenCalledWith('/api/api-testing/v1/baselines/baseline-1')
    expect(store.items).toEqual([])
    expect(store.selectedIds).toEqual([])
  })
})
