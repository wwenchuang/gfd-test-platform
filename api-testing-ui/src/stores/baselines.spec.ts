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
        environment_revision_id: 'environment-1', endpoint_id: 'endpoint-1', case_name: '我的收藏列表',
        case_version: 2, priority: 'P1', origin: 'ai', method: 'POST', path: '/print3d/api/v1/collection/page',
        endpoint_summary: '我的收藏列表', tags: ['家用业务', 'app接口'], adoption_reason: 'passing debug evidence',
        group_name: '我的收藏', adopted_at: '2026-08-12T10:00:00Z',
      },
      {
        id: 'baseline-2', project_id: 'project-1', case_id: 'case-2', case_version_id: 'version-2',
        environment_revision_id: 'environment-1', endpoint_id: 'endpoint-2', case_name: '取消收藏',
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

    expect(get).toHaveBeenCalledWith('/api/api-testing/v1/baselines?project_id=project-1&source_revision_id=source-1&environment_revision_id=environment-1')
    expect(store.groups).toEqual(['我的收藏'])
    expect(store.selectedEndpointIds).toEqual(['endpoint-1', 'endpoint-2'])
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
        environment_revision_id: 'environment-1', endpoint_id: 'endpoint-1', case_name: '我的收藏列表',
        case_version: 2, priority: 'P1', origin: 'ai', method: 'POST', path: '/print3d/api/v1/collection/page',
        endpoint_summary: '我的收藏列表', tags: [], group_name: '未分组', adoption_reason: '',
        adopted_at: '2026-08-12T10:00:00Z',
      },
      {
        id: 'baseline-2', project_id: 'project-1', case_id: 'case-2', case_version_id: 'version-2',
        environment_revision_id: 'environment-1', endpoint_id: 'endpoint-2', case_name: '取消收藏',
        case_version: 1, priority: 'P1', origin: 'manual', method: 'POST', path: '/print3d/api/v1/collection/cancel',
        endpoint_summary: '取消收藏', tags: [], group_name: '未分组', adoption_reason: '',
        adopted_at: '2026-08-12T10:01:00Z',
      },
    ]
    store.select(['baseline-1', 'baseline-2'])

    await store.updateGroup(store.selectedIds, '发版冒烟')

    expect(apiClient.post).toHaveBeenCalledWith('/api/api-testing/v1/baselines/bulk-group', {
      baseline_ids: ['baseline-1', 'baseline-2'],
      group_name: '发版冒烟',
    })
    expect(store.groups).toEqual(['发版冒烟'])
  })

  it('archives a baseline without deleting the reusable case draft', async () => {
    vi.spyOn(apiClient, 'delete').mockResolvedValue({ data: { baseline: { id: 'baseline-1', status: 'archived' } } })
    const store = useBaselinesStore()
    store.items = [
      {
        id: 'baseline-1', project_id: 'project-1', case_id: 'case-1', case_version_id: 'version-1',
        environment_revision_id: 'environment-1', endpoint_id: 'endpoint-1', case_name: '我的收藏列表',
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
