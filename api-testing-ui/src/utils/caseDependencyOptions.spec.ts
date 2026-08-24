import { describe, expect, it } from 'vitest'

import { buildCaseDependencyOptions } from './caseDependencyOptions'
import type { ApiEndpoint, CaseVersion } from '../api/contracts'

function caseVersion(overrides: Partial<CaseVersion>): CaseVersion {
  return {
    id: 'version', case_id: 'case', endpoint_id: 'endpoint', status: 'active', origin: 'manual', version: 1,
    group_name: '', validation_summary: {}, name: '用例', purpose: '', priority: 'P1',
    request: { method: 'GET', path: '/', service: 'default', path_params: {}, query: {}, headers: {}, cookies: {}, body: null },
    data_rows: [], assertions: [], extractions: [], dependencies: [], processing: { pre: [], post: [] },
    ...overrides,
  }
}

describe('buildCaseDependencyOptions', () => {
  it('uses custom groups first and exposes extraction targets', () => {
    const versions = [caseVersion({
      id: 'version-1',
      case_id: 'case-1',
      endpoint_id: 'endpoint-1',
      name: '添加收藏',
      group_name: '稳定前置',
      version: 2,
      extractions: [{ target: 'favoriteSn' }, { target: 'modelSn' }],
    })]
    const endpoints = [{
      id: 'endpoint-1', method: 'POST', path: '/collection/add', summary: '添加收藏',
      tags: ['家用业务', 'app接口', '我的收藏'],
    }] as ApiEndpoint[]

    expect(buildCaseDependencyOptions(versions, endpoints)).toEqual([{
      id: 'version-1',
      name: '添加收藏',
      group: '稳定前置',
      method: 'POST',
      path: '/collection/add',
      version: 2,
      exports: ['favoriteSn', 'modelSn'],
    }])
  })

  it('falls back to the interface directory and excludes the current version', () => {
    const versions = [
      caseVersion({ id: 'current', endpoint_id: 'endpoint-1', name: '当前用例' }),
      caseVersion({ id: 'setup', endpoint_id: 'endpoint-2', name: '查询模型' }),
    ]
    const endpoints = [
      { id: 'endpoint-1', method: 'POST', path: '/target', summary: '当前用例', tags: ['家用业务'] },
      { id: 'endpoint-2', method: 'GET', path: '/models', summary: '查询模型', tags: ['家用业务', 'app接口', '模型库'] },
    ] as ApiEndpoint[]

    expect(buildCaseDependencyOptions(versions, endpoints, 'current')).toMatchObject([
      { id: 'setup', group: 'app接口 / 模型库' },
    ])
  })
})
