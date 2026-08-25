import { describe, expect, it } from 'vitest'

import type { CaseDependencyOption, CaseDraft } from '../api/contracts'
import { withLegacyVariables, workflowVariableOptions } from './workflowVariables'

const DEPENDENCIES: CaseDependencyOption[] = [{
  id: 'login-version', name: '登录并获取令牌', group: '账号', method: 'POST', path: '/login', version: 2,
  exports: ['loginToken'],
}]

const DRAFT: CaseDraft = {
  name: '打印链路', purpose: '', priority: 'P1',
  request: { method: 'POST', path: '/print', service: 'default', path_params: {}, query: {}, headers: {}, cookies: {}, body: null },
  data_rows: [], assertions: [],
  extractions: [{ target: 'printTaskSn', type: 'json_path', path: '$.data.taskSn', required: true }],
  dependencies: [{ case_version_id: 'login-version', required: true, exports: ['loginToken'] }],
  processing: {
    pre: [], post: [],
    setup_steps: [
      { name: '登录', enabled: true, request: { method: 'POST', path: '/login', service: 'default', path_params: {}, query: {}, headers: {}, cookies: {}, body: null }, assertions: [], extractions: [{ target: 'sessionId' }], required_variables: [] },
      { name: '查询模型', enabled: true, request: { method: 'GET', path: '/models', service: 'default', path_params: {}, query: {}, headers: {}, cookies: {}, body: null }, assertions: [], extractions: [{ target: 'modelSn' }], required_variables: ['sessionId'] },
      { name: '禁用步骤', enabled: false, request: { method: 'GET', path: '/disabled', service: 'default', path_params: {}, query: {}, headers: {}, cookies: {}, body: null }, assertions: [], extractions: [{ target: 'disabledId' }], required_variables: [] },
    ],
    cleanup_steps: [],
  },
}

describe('workflow variables', () => {
  it('exposes only variables produced before a setup step', () => {
    const options = workflowVariableOptions(DRAFT, 'setup', 1, ['Biz'], DEPENDENCIES)
    expect(options.map(item => item.name)).toEqual(expect.arrayContaining(['Biz', 'loginToken', 'sessionId']))
    expect(options.find(item => item.name === 'modelSn')).toBeUndefined()
    expect(options.find(item => item.name === 'printTaskSn')).toBeUndefined()
    expect(options.find(item => item.name === 'disabledId')).toBeUndefined()
  })

  it('exposes main extractions to cleanup and preserves unknown legacy names', () => {
    const options = workflowVariableOptions(DRAFT, 'cleanup', 0, [], DEPENDENCIES)
    expect(options.find(item => item.name === 'printTaskSn')?.sourceKind).toBe('main')
    expect(withLegacyVariables(options, ['legacyId'])).toContainEqual(expect.objectContaining({
      name: 'legacyId', sourceKind: 'unknown', available: false,
    }))
  })

  it('attributes an overwritten variable to its latest available producer', () => {
    const draft = JSON.parse(JSON.stringify(DRAFT)) as CaseDraft
    draft.processing.setup_steps![0].extractions = [{ target: 'Biz' }]

    const option = workflowVariableOptions(draft, 'main', 0, ['Biz'], DEPENDENCIES).find(item => item.name === 'Biz')

    expect(option).toMatchObject({ sourceKind: 'setup', source: '前置步骤 1 · 登录' })
  })
})
