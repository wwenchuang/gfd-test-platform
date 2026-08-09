import type { ApiEnvelope } from './contracts'

export class ApiClientError extends Error {
  constructor(public readonly status: number, message: string) {
    super(message)
  }
}

type RequestOptions = Omit<RequestInit, 'body'> & { body?: unknown }

const LOGIN_PATH = '/task-manager.html?return_to=%2Fapi-test%2F'

export class ApiClient {
  async get<T>(path: string): Promise<ApiEnvelope<T>> {
    return this.request<T>(path)
  }

  async put<T>(path: string, body: unknown): Promise<ApiEnvelope<T>> {
    return this.request<T>(path, { method: 'PUT', body })
  }

  async post<T>(path: string, body: unknown): Promise<ApiEnvelope<T>> {
    return this.request<T>(path, { method: 'POST', body })
  }

  private async request<T>(path: string, options: RequestOptions = {}): Promise<ApiEnvelope<T>> {
    const token = sessionStorage.getItem('sessionToken')
    if (!token) {
      this.redirectToLogin()
      throw new ApiClientError(401, '登录已失效')
    }

    const headers = new Headers(options.headers)
    headers.set('Authorization', `Bearer ${token}`)
    if (options.body !== undefined) headers.set('Content-Type', 'application/json')

    const response = await fetch(path, {
      ...options,
      headers,
      body: options.body === undefined ? undefined : JSON.stringify(options.body),
      credentials: 'same-origin',
    })
    if (response.status === 401) {
      sessionStorage.removeItem('sessionToken')
      sessionStorage.removeItem('user')
      this.redirectToLogin()
      throw new ApiClientError(401, '登录已失效')
    }
    const payload = await response.json() as ApiEnvelope<T> & { error?: unknown; message?: unknown }
    if (!response.ok) throw new ApiClientError(response.status, errorMessage(payload))
    return payload
  }

  private redirectToLogin(): void {
    window.location.assign(LOGIN_PATH)
  }
}

function errorMessage(payload: { error?: unknown; message?: unknown }): string {
  if (typeof payload.error === 'string') return payload.error
  if (typeof payload.message === 'string') return payload.message
  if (payload.error && typeof payload.error === 'object' && 'message' in payload.error) {
    const message = (payload.error as { message?: unknown }).message
    if (typeof message === 'string') return message
  }
  return '请求失败'
}

export const apiClient = new ApiClient()
