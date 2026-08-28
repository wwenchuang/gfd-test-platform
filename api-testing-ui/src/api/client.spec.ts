import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { ApiClient, ApiClientError } from './client'

const LOGIN_PATH = '/task-manager.html?return_to=%2Fapi-test%2F'

function response(status: number, body: unknown): Response {
  const serialized = typeof body === 'string' ? body : JSON.stringify(body)
  return {
    status,
    ok: status >= 200 && status < 300,
    json: async () => body,
    text: async () => serialized,
    headers: new Headers({ 'Content-Type': 'application/json; charset=utf-8' }),
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

  it('aborts an unresponsive request and returns a Chinese recovery message', async () => {
    vi.useFakeTimers()
    values.set('sessionToken', 'session-value')
    vi.mocked(fetch).mockImplementation((_path, options) => new Promise((_resolve, reject) => {
      options?.signal?.addEventListener('abort', () => reject(new DOMException('Aborted', 'AbortError')))
    }))

    const result = expect(new ApiClient(1_000).get('/api/api-testing/v1/workspace')).rejects.toMatchObject({
      status: 408,
      message: expect.stringContaining('服务响应超时'),
    })
    await vi.advanceTimersByTimeAsync(1_000)
    await result
    vi.useRealTimers()
  })

  it('localizes a network connection failure', async () => {
    values.set('sessionToken', 'session-value')
    vi.mocked(fetch).mockRejectedValue(new TypeError('Failed to fetch'))

    await expect(new ApiClient().get('/api/api-testing/v1/workspace')).rejects.toMatchObject({
      status: 0,
      message: expect.stringContaining('无法连接测试服务'),
    })
  })

  it('explains an HTML 502 response as a backend deployment or restart problem', async () => {
    values.set('sessionToken', 'session-value')
    vi.mocked(fetch).mockResolvedValue({
      status: 502,
      ok: false,
      headers: new Headers({ 'Content-Type': 'text/html' }),
      text: async () => '<html><h1>502 Bad Gateway</h1></html>',
    } as Response)

    await expect(new ApiClient().get('/api/api-testing/v1/cases')).rejects.toMatchObject({
      status: 502,
      message: expect.stringMatching(/后端服务暂不可用.*部署或重启.*刷新.*管理员/),
    })
  })

  it('explains a non-JSON 404 as a possible frontend/backend version mismatch', async () => {
    values.set('sessionToken', 'session-value')
    vi.mocked(fetch).mockResolvedValue({
      status: 404,
      ok: false,
      headers: new Headers({ 'Content-Type': 'text/html' }),
      text: async () => '<html><h1>Not Found</h1></html>',
    } as Response)

    await expect(new ApiClient().get('/api/api-testing/v1/new-route')).rejects.toMatchObject({
      status: 404,
      message: expect.stringMatching(/接口地址不存在.*前后端版本.*重新部署/),
    })
  })
})
