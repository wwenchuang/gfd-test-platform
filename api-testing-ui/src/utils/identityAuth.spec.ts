import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import * as auth from './authRedirect'
import { ApiClient } from '../api/client'

describe('local identity integration', () => {
  const values = new Map<string, string>()
  const assign = vi.fn()
  beforeEach(() => {
    auth.setApiTestingAccessProfile(null)
    values.clear()
    values.set('sessionToken', 'fixture-token')
    assign.mockReset()
    vi.stubGlobal('sessionStorage', { getItem: (key: string) => values.get(key) ?? null, setItem: (key: string, value: string) => values.set(key, value), removeItem: (key: string) => values.delete(key) })
    vi.stubGlobal('window', { location: { pathname: '/api-test/', hash: '#/reports?projectId=p1', assign } })
    vi.stubGlobal('fetch', vi.fn())
  })
  afterEach(() => vi.unstubAllGlobals())

  it('verifies me before bootstrap and routes must-change users to main', async () => {
    vi.mocked(fetch).mockResolvedValue(new Response(JSON.stringify({ ok: true, user: 'tester', profile: { must_change_password: true } })))
    expect(auth).toHaveProperty('verifyApiTestingSession')
    const verify = (auth as unknown as { verifyApiTestingSession: () => Promise<boolean> }).verifyApiTestingSession
    expect(await verify()).toBe(false)
    expect(assign).toHaveBeenCalledWith('/task-manager.html?return_to=%2Fapi-test%2F%23%2Freports%3FprojectId%3Dp1')
    expect(values.get('sessionToken')).toBe('fixture-token')
  })

  it('retains the verified profile for early production permission feedback', async () => {
    vi.mocked(fetch).mockResolvedValue(new Response(JSON.stringify({
      ok: true,
      user: 'tester',
      profile: { status: 'active', permissions: ['api.view', 'api.execute'] },
    })))

    expect(await auth.verifyApiTestingSession()).toBe(true)
    expect(auth.apiTestingHasPermission('api.execute')).toBe(true)
    expect(auth.apiTestingHasPermission('api.production')).toBe(false)
  })

  it('ordinary 403 keeps login and offers a remedy', async () => {
    vi.mocked(fetch).mockResolvedValue(new Response(JSON.stringify({ error: { message: '缺少 api.edit' } }), { status: 403 }))
    await expect(new ApiClient().post('/api/api-testing/v1/cases', {})).rejects.toMatchObject({ status: 403, message: expect.stringMatching(/api.edit.*联系管理员/) })
    expect(assign).not.toHaveBeenCalled()
    expect(values.get('sessionToken')).toBe('fixture-token')
  })

  it('a business password gate redirects without dropping the usable token', async () => {
    vi.mocked(fetch).mockResolvedValue(new Response(JSON.stringify({ error: { code: 'must_change_password', message: '请修改密码' } }), { status: 403 }))
    await expect(new ApiClient().get('/api/api-testing/v1/projects')).rejects.toMatchObject({ status: 403 })
    expect(assign).toHaveBeenCalledWith(auth.apiTestingLoginPath())
    expect(values.get('sessionToken')).toBe('fixture-token')
  })
})
