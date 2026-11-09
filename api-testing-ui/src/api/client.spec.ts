import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { ApiClient, ApiClientError } from './client'

const LOGIN_PATH = '/task-manager.html?return_to=%2Fapi-test%2F%23%2Fbaselines'

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
    vi.stubGlobal('window', {
      location: {
        pathname: '/api-test/',
        hash: '#/baselines',
        assign,
      },
    })
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

  it('redirects a logged-out deep link to login before sending any request', async () => {
    await expect(new ApiClient().get('/api/api-testing/v1/baselines')).rejects.toMatchObject({
      status: 401,
      message: '登录已失效',
    })

    expect(fetch).not.toHaveBeenCalled()
    expect(assign).toHaveBeenCalledWith(LOGIN_PATH)
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

  it('does not clear or redirect a newer session when an older request returns 401', async () => {
    values.set('sessionToken', 'old-session')
    values.set('user', 'old-user')
    let finish!: (result: Response) => void
    vi.mocked(fetch).mockImplementation(() => new Promise(resolve => { finish = resolve }))
    const pending = new ApiClient().get('/api/api-testing/v1/workspace')
    values.set('sessionToken', 'new-session')
    values.set('user', 'new-user')
    finish(response(401, { error: { message: 'expired' } }))
    await expect(pending).rejects.toMatchObject({ message: expect.stringContaining('登录状态已变化') })
    expect(values.get('sessionToken')).toBe('new-session')
    expect(values.get('user')).toBe('new-user')
    expect(assign).not.toHaveBeenCalled()
  })

  it('does not redirect a newer session for an older password-change response', async () => {
    values.set('sessionToken', 'old-session')
    let finish!: (result: Response) => void
    vi.mocked(fetch).mockImplementation(() => new Promise(resolve => { finish = resolve }))
    const pending = new ApiClient().get('/api/api-testing/v1/workspace')
    values.set('sessionToken', 'new-session')
    finish(response(403, { code: 'password_change_required' }))
    await expect(pending).rejects.toMatchObject({ message: expect.stringContaining('登录状态已变化') })
    expect(values.get('sessionToken')).toBe('new-session')
    expect(assign).not.toHaveBeenCalled()
  })

  it('does not deliver another session’s result when the session changes while reading the body', async () => {
    values.set('sessionToken', 'old-session')
    let finishBody!: (value: string) => void
    vi.mocked(fetch).mockResolvedValue({ status: 200, ok: true, text: () => new Promise(resolve => { finishBody = resolve }) } as Response)
    const pending = new ApiClient().get('/api/api-testing/v1/workspace')
    await Promise.resolve()
    values.set('sessionToken', 'new-session')
    finishBody(JSON.stringify({ data: { secret: 'old-account-data' } }))
    await expect(pending).rejects.toMatchObject({ message: expect.stringContaining('登录状态已变化') })
    expect(assign).not.toHaveBeenCalled()
  })

  it('warns against repeating a write that returned after the session changed', async () => {
    values.set('sessionToken', 'old-session')
    let finish!: (result: Response) => void
    vi.mocked(fetch).mockImplementation(() => new Promise(resolve => { finish = resolve }))
    const pending = new ApiClient().post('/api/api-testing/v1/cases', { name: 'draft' })
    values.set('sessionToken', 'new-session')
    finish(response(200, { data: { saved: true } }))
    await expect(pending).rejects.toMatchObject({ message: expect.stringMatching(/登录状态已变化.*可能已提交.*避免重复/) })
    expect(assign).not.toHaveBeenCalled()
  })

  it.each(['PUT', 'DELETE'] as const)('does not deliver an old-session %s result and warns about a possible commit', async method => {
    values.set('sessionToken', 'old-session')
    let finish!: (result: Response) => void
    vi.mocked(fetch).mockImplementation(() => new Promise(resolve => { finish = resolve }))
    const client = new ApiClient()
    const pending = method === 'PUT'
      ? client.put('/api/api-testing/v1/cases/1', { name: 'draft' })
      : client.delete('/api/api-testing/v1/cases/1')
    values.set('sessionToken', 'new-session')
    finish(response(200, { data: { saved: true } }))
    await expect(pending).rejects.toMatchObject({ status: 409, message: expect.stringMatching(/登录状态已变化.*可能已提交.*避免重复/) })
    expect(values.get('sessionToken')).toBe('new-session')
    expect(assign).not.toHaveBeenCalled()
  })

  it('requests a fresh read without a commit warning for a read-only POST after a session change', async () => {
    values.set('sessionToken', 'old-session')
    let finish!: (result: Response) => void
    vi.mocked(fetch).mockImplementation(() => new Promise(resolve => { finish = resolve }))
    const pending = new ApiClient().post('/api/api-testing/v1/providers/apifox/context', {}, { readOnly: true })
    values.set('sessionToken', 'new-session')
    finish(response(200, { data: { context: {} } }))
    await expect(pending).rejects.toMatchObject({ status: 409, message: '登录状态已变化，请在当前账号下重新读取' })
    expect(assign).not.toHaveBeenCalled()
  })

  it('does not expose an old-session network failure as a current-session failure', async () => {
    values.set('sessionToken', 'old-session')
    let fail!: (error: Error) => void
    vi.mocked(fetch).mockImplementation(() => new Promise((_resolve, reject) => { fail = reject }))
    const pending = new ApiClient().get('/api/api-testing/v1/workspace')
    values.set('sessionToken', 'new-session')
    fail(new TypeError('Failed to fetch'))
    await expect(pending).rejects.toMatchObject({ status: 409, message: '登录状态已变化，请在当前账号下重新读取' })
    expect(assign).not.toHaveBeenCalled()
  })

  it('warns an old-session write may have been submitted when the connection fails', async () => {
    values.set('sessionToken', 'old-session')
    let fail!: (error: Error) => void
    vi.mocked(fetch).mockImplementation(() => new Promise((_resolve, reject) => { fail = reject }))
    const pending = new ApiClient().put('/api/api-testing/v1/cases/1', { name: 'draft' })
    values.set('sessionToken', 'new-session')
    fail(new TypeError('Failed to fetch'))
    await expect(pending).rejects.toMatchObject({ status: 409, message: expect.stringMatching(/登录状态已变化.*可能已提交.*避免重复/) })
    expect(assign).not.toHaveBeenCalled()
  })

  it('warns a timed-out old-session write may have been submitted after the session changes', async () => {
    vi.useFakeTimers()
    try {
      values.set('sessionToken', 'old-session')
      vi.mocked(fetch).mockImplementation((_path, options) => new Promise((_resolve, reject) => {
        options?.signal?.addEventListener('abort', () => reject(new DOMException('Aborted', 'AbortError')))
      }))
      const result = expect(new ApiClient(1_000).post('/api/api-testing/v1/cases', { name: 'draft' })).rejects.toMatchObject({
        status: 409, message: expect.stringMatching(/登录状态已变化.*可能已提交.*避免重复/),
      })
      values.set('sessionToken', 'new-session')
      await vi.advanceTimersByTimeAsync(1_000)
      await result
      expect(values.get('sessionToken')).toBe('new-session')
      expect(assign).not.toHaveBeenCalled()
    } finally {
      vi.useRealTimers()
    }
  })

  it('treats a timed-out old-session read-only POST as a read after a session change', async () => {
    vi.useFakeTimers()
    try {
      values.set('sessionToken', 'old-session')
      vi.mocked(fetch).mockImplementation((_path, options) => new Promise((_resolve, reject) => {
        options?.signal?.addEventListener('abort', () => reject(new DOMException('Aborted', 'AbortError')))
      }))
      const result = expect(new ApiClient(1_000).post('/api/api-testing/v1/providers/apifox/context', {}, { readOnly: true })).rejects.toMatchObject({
        status: 409, message: '登录状态已变化，请在当前账号下重新读取',
      })
      values.set('sessionToken', 'new-session')
      await vi.advanceTimersByTimeAsync(1_000)
      await result
      expect(assign).not.toHaveBeenCalled()
    } finally {
      vi.useRealTimers()
    }
  })

  it('does not expose an old-session body read failure as a current-session error', async () => {
    values.set('sessionToken', 'old-session')
    let failBody!: (error: Error) => void
    vi.mocked(fetch).mockResolvedValue({ status: 200, ok: true, text: () => new Promise((_resolve, reject) => { failBody = reject }) } as Response)
    const pending = new ApiClient().get('/api/api-testing/v1/workspace')
    await Promise.resolve()
    values.set('sessionToken', 'new-session')
    failBody(new Error('body disconnected'))
    await expect(pending).rejects.toMatchObject({ status: 409, message: '登录状态已变化，请在当前账号下重新读取' })
    expect(assign).not.toHaveBeenCalled()
  })

  it('retains current-session password-change redirection', async () => {
    values.set('sessionToken', 'current-session')
    vi.mocked(fetch).mockResolvedValue(response(403, { code: 'password_change_required' }))
    await expect(new ApiClient().get('/api/api-testing/v1/workspace')).rejects.toMatchObject({ status: 403 })
    expect(values.get('sessionToken')).toBe('current-session')
    expect(assign).toHaveBeenCalledWith(LOGIN_PATH)
  })

  it('does not redirect when a password-change body finishes after a session change', async () => {
    values.set('sessionToken', 'old-session')
    let finishBody!: (value: string) => void
    vi.mocked(fetch).mockResolvedValue({ status: 403, ok: false, text: () => new Promise(resolve => { finishBody = resolve }) } as Response)
    const pending = new ApiClient().get('/api/api-testing/v1/workspace')
    await Promise.resolve()
    values.set('sessionToken', 'new-session')
    finishBody(JSON.stringify({ code: 'password_change_required' }))
    await expect(pending).rejects.toMatchObject({ message: expect.stringContaining('登录状态已变化') })
    expect(values.get('sessionToken')).toBe('new-session')
    expect(assign).not.toHaveBeenCalled()
  })

  it('uses an object error envelope message for non-auth failures', async () => {
    values.set('sessionToken', 'session-value')
    vi.mocked(fetch).mockResolvedValue(response(422, { error: { message: '工作区字段无效' } }))

    await expect(new ApiClient().get('/api/api-testing/v1/workspace')).rejects.toMatchObject({
      status: 422,
      message: '工作区字段无效',
    })
  })

  it('explains that a timed-out read request did not submit changes', async () => {
    vi.useFakeTimers()
    values.set('sessionToken', 'session-value')
    vi.mocked(fetch).mockImplementation((_path, options) => new Promise((_resolve, reject) => {
      options?.signal?.addEventListener('abort', () => reject(new DOMException('Aborted', 'AbortError')))
    }))

    const result = expect(new ApiClient(1_000).get('/api/api-testing/v1/workspace')).rejects.toMatchObject({
      status: 408,
      message: expect.stringMatching(/读取超时.*不会提交或修改数据.*重试/),
    })
    await vi.advanceTimersByTimeAsync(1_000)
    await result
    vi.useRealTimers()
  })

  it('warns that a timed-out write may already have been submitted', async () => {
    vi.useFakeTimers()
    values.set('sessionToken', 'session-value')
    vi.mocked(fetch).mockImplementation((_path, options) => new Promise((_resolve, reject) => {
      options?.signal?.addEventListener('abort', () => reject(new DOMException('Aborted', 'AbortError')))
    }))

    const result = expect(new ApiClient(1_000).post('/api/api-testing/v1/cases', { name: 'draft' })).rejects.toMatchObject({
      status: 408,
      message: expect.stringMatching(/提交超时.*可能已经提交.*刷新对应列表/),
    })
    await vi.advanceTimersByTimeAsync(1_000)
    await result
    vi.useRealTimers()
  })

  it('describes an explicitly read-only POST timeout without suggesting a sync was submitted', async () => {
    vi.useFakeTimers()
    values.set('sessionToken', 'session-value')
    vi.mocked(fetch).mockImplementation((_path, options) => new Promise((_resolve, reject) => {
      options?.signal?.addEventListener('abort', () => reject(new DOMException('Aborted', 'AbortError')))
    }))
    const result = expect(new ApiClient(1_000).post('/api/api-testing/v1/providers/apifox/context', {}, { readOnly: true })).rejects.toMatchObject({
      status: 408,
      message: expect.stringMatching(/读取超时.*不会提交或修改数据.*重试/),
    })
    await vi.advanceTimersByTimeAsync(1_000)
    await result
    expect(vi.mocked(fetch).mock.calls[0][1]).not.toHaveProperty('readOnly')
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
