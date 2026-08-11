import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { ApiClient, ApiClientError } from './client'

const LOGIN_PATH = '/task-manager.html?return_to=%2Fapi-test%2F'

function response(status: number, body: unknown): Response {
  return {
    status,
    ok: status >= 200 && status < 300,
    json: async () => body,
  } as Response
}

describe('ApiClient', () => {
  const values = new Map<string, string>()
  const assign = vi.fn()

  beforeEach(() => {
    values.clear()
    assign.mockReset()
    vi.stubGlobal('sessionStorage', {
      getItem: (key: string) => values.get(key) ?? null,
      setItem: (key: string, value: string) => values.set(key, value),
      removeItem: (key: string) => values.delete(key),
    })
    vi.stubGlobal('window', { location: { assign } })
    vi.stubGlobal('fetch', vi.fn())
  })

  afterEach(() => vi.unstubAllGlobals())

  it('sends the session token as a Bearer authorization header', async () => {
    values.set('sessionToken', 'session-value')
    vi.mocked(fetch).mockResolvedValue(response(200, { data: { ready: true } }))

    await new ApiClient().get('/api/api-testing/v1/workspace')

    const [, options] = vi.mocked(fetch).mock.calls[0]
    expect((options?.headers as Headers).get('Authorization')).toBe('Bearer session-value')
  })

  it('clears only session auth and redirects in the same tab after a 401', async () => {
    values.set('sessionToken', 'expired')
    values.set('user', 'tester')
    values.set('businessToken', 'must-remain')
    vi.mocked(fetch).mockResolvedValue(response(401, { error: { message: '会话已过期' } }))

    await expect(new ApiClient().get('/api/api-testing/v1/workspace')).rejects.toBeInstanceOf(ApiClientError)

    expect(values.has('sessionToken')).toBe(false)
    expect(values.has('user')).toBe(false)
    expect(values.get('businessToken')).toBe('must-remain')
    expect(assign).toHaveBeenCalledWith(LOGIN_PATH)
  })

  it('uses an object error envelope message for non-auth failures', async () => {
    values.set('sessionToken', 'session-value')
    vi.mocked(fetch).mockResolvedValue(response(422, { error: { message: '工作区字段无效' } }))

    await expect(new ApiClient().get('/api/api-testing/v1/workspace')).rejects.toMatchObject({
      status: 422,
      message: '工作区字段无效',
    })
  })
})
