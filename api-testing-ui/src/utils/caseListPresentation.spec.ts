import { describe, expect, it } from 'vitest'

import type { ApiEndpoint, CaseVersion, GeneratedCasePreview } from '../api/contracts'
import {
  buildCaseGroupTree,
  caseSearchText,
  matchesCaseWorkView,
  type CaseListItem,
} from './caseListPresentation'

function endpoint(id: string, path: string, tags: string[] = []): ApiEndpoint {
  return { id, method: 'GET', path, summary: id, tags }
}

function versionItem(id: string, groupName: string, processing: CaseVersion['processing'] = { pre: [], post: [] }): CaseListItem {
  const version = {
    id,
    case_id: `case-${id}`,
    endpoint_id: `endpoint-${id}`,
    status: 'draft',
    origin: 'manual',
    version: 1,
    group_name: groupName,
    validation_summary: {},
    name: `${id} 用例`,
    purpose: '测试',
    priority: 'P1',
    request: { method: 'GET', path: `/${id}`, service: 'default', path_params: {}, query: {}, headers: {}, cookies: {}, body: null },
    data_rows: [],
    assertions: [],
    extractions: [],
    dependencies: [],
    processing,
  } as CaseVersion
  return {
    kind: 'version',
    id,
    endpoint: endpoint(version.endpoint_id, version.request.path, groupName.split('/').map(part => part.trim())),
    name: version.name,
    meta: 'v1 · 手工',
    groupName,
    version,
  }
}

function previewItem(id: string, groupName: string): CaseListItem {
  const preview = {
    id,
    endpoint_id: `endpoint-${id}`,
    origin: 'ai',
    case: {
      name: `${id} 候选`,
      purpose: '测试',
      priority: 'P1',
      request: { method: 'POST', path: `/${id}`, service: 'default', path_params: {}, query: {}, headers: {}, cookies: {}, body: null },
      data_rows: [], assertions: [], extractions: [], dependencies: [], processing: { pre: [], post: [] },
    },
  } as GeneratedCasePreview
  return {
    kind: 'preview',
    id,
    endpoint: { ...endpoint(preview.endpoint_id, preview.case.request.path), method: 'POST' },
    name: preview.case.name,
    meta: '候选 · AI',
    groupName,
    preview,
  }
}

describe('case list presentation', () => {
  it('builds a recursive tree, merges ancestors and counts descendants', () => {
    const tree = buildCaseGroupTree([
      versionItem('direct', '家用业务'),
      versionItem('favorite', '家用业务 / app接口 / 我的收藏'),
      versionItem('device', '家用业务 / app接口 / 设备'),
      versionItem('admin', '后台管理 / 用户'),
    ])

    expect(tree.map(node => node.label)).toEqual(['后台管理', '家用业务'])
    const home = tree[1]
    expect(home.count).toBe(3)
    expect(home.items.map(item => item.id)).toEqual(['direct'])
    expect(home.children[0]).toMatchObject({ label: 'app接口', fullPath: '家用业务 / app接口', count: 2 })
    expect(home.children[0].children.map(node => node.label)).toEqual(['设备', '我的收藏'])
  })

  it('normalizes empty and repeated path separators', () => {
    const tree = buildCaseGroupTree([
      versionItem('one', ' 家用业务 // app接口 / 我的 '),
      versionItem('two', ''),
    ])

    expect(tree.map(node => node.label)).toEqual(['家用业务', '未分组用例'])
    expect(tree[0].children[0].children[0].fullPath).toBe('家用业务 / app接口 / 我的')
  })

  it('classifies task, orchestrated, one-time, and candidate work views', () => {
    const normal = versionItem('normal', '家用业务 / 我的')
    const orchestrated = versionItem('workflow', '家用业务 / 模型', {
      pre: [],
      post: [],
      setup_steps: [{
        name: '查询模型', enabled: true,
        request: { method: 'GET', path: '/models', service: 'default', path_params: {}, query: {}, headers: {}, cookies: {}, body: null },
        assertions: [], extractions: [], required_variables: [],
      }],
    })
    const disabledOnly = versionItem('disabled', '家用业务 / 模型', {
      pre: [],
      post: [],
      cleanup_steps: [{
        name: '关闭的清理', enabled: false,
        request: { method: 'DELETE', path: '/models/1', service: 'default', path_params: {}, query: {}, headers: {}, cookies: {}, body: null },
        assertions: [], extractions: [], required_variables: [],
      }],
    })
    const oneTime = versionItem('api-test', 'API Test / 一次性')
    const preview = previewItem('candidate', '家用业务 / 我的')
    const selected = new Set(['endpoint-normal'])

    expect(matchesCaseWorkView(normal, 'task', selected)).toBe(true)
    expect(matchesCaseWorkView(orchestrated, 'orchestrated', selected)).toBe(true)
    expect(matchesCaseWorkView(disabledOnly, 'orchestrated', selected)).toBe(false)
    expect(matchesCaseWorkView(oneTime, 'one-time', selected)).toBe(true)
    expect(matchesCaseWorkView(normal, 'one-time', selected)).toBe(false)
    expect(matchesCaseWorkView(preview, 'candidate', selected)).toBe(true)
    expect(matchesCaseWorkView(normal, 'candidate', selected)).toBe(false)
    expect(matchesCaseWorkView(preview, 'all', selected)).toBe(true)
  })

  it('builds searchable text from group, case, method, path and metadata', () => {
    const item = versionItem('favorite', '家用业务 / 我的收藏')
    expect(caseSearchText(item)).toContain('家用业务 / 我的收藏')
    expect(caseSearchText(item)).toContain('favorite 用例')
    expect(caseSearchText(item)).toContain('GET')
    expect(caseSearchText(item)).toContain('/favorite')
    expect(caseSearchText(item)).toContain('v1 · 手工')
  })
})
