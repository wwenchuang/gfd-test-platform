import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { apiClient } from '../api/client'
import {
  businessLineLabel,
  loadBusinessLines,
  normalizeBusinessLines,
  replaceBusinessLines,
  useBusinessLines,
} from './businessLines'

describe('business line presentation', () => {
  beforeEach(() => replaceBusinessLines([
    { id: 'home', name: '家用', enabled: true },
    { id: 'shared', name: '共享', enabled: true },
  ]))
  afterEach(() => {
    vi.restoreAllMocks()
    vi.unstubAllGlobals()
  })

  it('uses configured Chinese names while keeping opaque ids internal', () => {
    replaceBusinessLines([
      { id: 'biz_school', name: '校园版', enabled: true },
      { id: 'home', name: '家庭版', enabled: false },
    ])

    expect(useBusinessLines().active.value).toEqual([
      { id: 'biz_school', name: '校园版', enabled: true },
    ])
    expect(businessLineLabel('biz_school')).toBe('校园版')
    expect(businessLineLabel('home')).toBe('家庭版')
  })

  it('falls back to the historical Chinese defaults for invalid payloads', () => {
    expect(normalizeBusinessLines([])).toEqual([
      { id: 'home', name: '家用', enabled: true },
      { id: 'shared', name: '共享', enabled: true },
    ])
  })

  it('loads application business lines from the task platform response', async () => {
    vi.stubGlobal('sessionStorage', { getItem: () => 'token' })
    vi.spyOn(apiClient, 'get').mockResolvedValue({ data: { apps: [{
      package: 'com.kfb.model',
      name: '智小白3D',
      business_lines: [{ id: 'biz_school', name: '校园版', enabled: true }],
    }] } } as never)

    await loadBusinessLines(true)

    expect(useBusinessLines().active.value.map(item => item.name)).toEqual(['校园版'])
  })
})
