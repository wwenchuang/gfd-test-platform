import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { apiClient } from '../api/client'
import {
  activeBusinessLinesFor,
  loadTestApplications,
  replaceTestApplications,
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
})
