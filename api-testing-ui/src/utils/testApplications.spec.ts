import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { apiClient } from '../api/client'
import {
  activeBusinessLinesFor,
  applicationBusinessLabel,
  applicationBusinessSelection,
  loadTestApplications,
  replaceTestApplications,
  testApplicationLabel,
  testApplicationFor,
  useTestApplications,
} from './testApplications'

describe('configured test applications', () => {
  beforeEach(() => replaceTestApplications([]))
  afterEach(() => {
    vi.restoreAllMocks()
    vi.unstubAllGlobals()
  })

  it('keeps applications and business choices isolated by package', () => {
    replaceTestApplications([
      { package: 'com.kfb.model', name: '智小白3D', enabled: true, business_lines: [{ id: 'home', name: '家用', enabled: true }] },
      { package: 'com.example.school', name: '校园版', enabled: true, business_lines: [{ id: 'school', name: '校园业务', enabled: true }] },
    ])

    expect(useTestApplications().active.value.map(item => item.name)).toEqual(['智小白3D', '校园版'])
    expect(testApplicationFor('com.example.school')?.name).toBe('校园版')
    expect(activeBusinessLinesFor('com.example.school').map(item => item.id)).toEqual(['school'])
  })

  it('loads active and disabled history entries from the task application response', async () => {
    vi.stubGlobal('sessionStorage', { getItem: () => 'token' })
    vi.spyOn(apiClient, 'get').mockResolvedValue({ data: { apps: [
      { package: 'com.kfb.model', name: '智小白3D', enabled: true, business_lines: [] },
      { package: 'com.example.retired', name: '旧版应用', enabled: false, business_lines: [] },
    ] } } as never)

    await loadTestApplications(true)

    expect(useTestApplications().all.value).toHaveLength(2)
    expect(useTestApplications().active.value.map(item => item.package)).toEqual(['com.kfb.model'])
  })

  it('renders configured application and package-scoped business names without internal identifiers', () => {
    replaceTestApplications([
      {
        package: 'com.example.retired', name: '历史应用新名称', enabled: false,
        business_lines: [{ id: 'shared', name: '历史共享', enabled: false }],
      },
      {
        package: 'com.example.school', name: '校园应用', enabled: true,
        business_lines: [{ id: 'shared', name: '校园共享', enabled: true }],
      },
    ])

    expect(testApplicationLabel('com.example.retired', '历史应用旧名称')).toBe('历史应用新名称')
    expect(applicationBusinessLabel('com.example.retired', '历史应用旧名称', 'shared')).toBe('历史应用新名称 · 历史共享')
    expect(applicationBusinessLabel('com.example.school', '校园应用', 'shared')).toBe('校园应用 · 校园共享')
    expect(applicationBusinessLabel('com.unknown.app', 'com.unknown.app', 'biz_internal')).toBe('未标注应用 · 未标注业务')
  })

  it('keeps disabled scopes readable but marks them unavailable for new selections', () => {
    replaceTestApplications([
      {
        package: 'com.example.retired', name: '历史应用', enabled: false,
        business_lines: [{ id: 'shared', name: '历史共享', enabled: true }],
      },
      {
        package: 'com.example.school', name: '校园应用', enabled: true,
        business_lines: [
          { id: 'active', name: '校园业务', enabled: true },
          { id: 'retired', name: '旧业务', enabled: false },
        ],
      },
    ])

    expect(applicationBusinessSelection('com.example.retired', 'shared')).toEqual({
      selectable: false,
      reason: '应用“历史应用”已停用',
    })
    expect(applicationBusinessSelection('com.example.school', 'retired')).toEqual({
      selectable: false,
      reason: '业务“旧业务”已停用',
    })
    expect(applicationBusinessSelection('com.example.school', 'active')).toEqual({
      selectable: true,
      reason: '',
    })
    expect(applicationBusinessSelection('com.unknown.app', 'unknown')).toEqual({
      selectable: false,
      reason: '应用未配置或已移除',
    })
  })
})
