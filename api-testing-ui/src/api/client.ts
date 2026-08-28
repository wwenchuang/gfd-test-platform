import type { ApiEnvelope } from './contracts'

export class ApiClientError extends Error {
  constructor(public readonly status: number, message: string) {
    super(message)
  }
}

type RequestOptions = Omit<RequestInit, 'body'> & { body?: unknown }

const LOGIN_PATH = '/task-manager.html?return_to=%2Fapi-test%2F'
const DEFAULT_TIMEOUT_MS = 30_000

export class ApiClient {
  constructor(private readonly timeoutMs = DEFAULT_TIMEOUT_MS) {}

  async get<T>(path: string): Promise<ApiEnvelope<T>> {
    return this.request<T>(path)
  }

  async put<T>(path: string, body: unknown): Promise<ApiEnvelope<T>> {
    return this.request<T>(path, { method: 'PUT', body })
  }

  async post<T>(path: string, body: unknown): Promise<ApiEnvelope<T>> {
    return this.request<T>(path, { method: 'POST', body })
  }

  async delete<T>(path: string): Promise<ApiEnvelope<T>> {
    return this.request<T>(path, { method: 'DELETE' })
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

    const controller = new AbortController()
    const upstreamSignal = options.signal
    let timedOut = false
    const abortFromUpstream = () => controller.abort(upstreamSignal?.reason)
    if (upstreamSignal?.aborted) abortFromUpstream()
    else upstreamSignal?.addEventListener('abort', abortFromUpstream, { once: true })
    const timeout = globalThis.setTimeout(() => {
      timedOut = true
      controller.abort()
    }, this.timeoutMs)

    let response: Response
    try {
      response = await fetch(path, {
        ...options,
        headers,
        body: options.body === undefined ? undefined : JSON.stringify(options.body),
        credentials: 'same-origin',
        signal: controller.signal,
      })
    } catch (error) {
      if (timedOut) {
        throw new ApiClientError(408, `服务响应超时（${formatTimeout(this.timeoutMs)}），请稍后重试；保存或执行操作可能已提交，可先刷新对应列表确认`)
      }
      if (isAbortError(error)) throw new ApiClientError(499, '请求已取消')
      throw new ApiClientError(0, '无法连接测试服务，请检查网络或服务状态后重试')
    } finally {
      globalThis.clearTimeout(timeout)
      upstreamSignal?.removeEventListener('abort', abortFromUpstream)
    }
    if (response.status === 401) {
      sessionStorage.removeItem('sessionToken')
      sessionStorage.removeItem('user')
      this.redirectToLogin()
      throw new ApiClientError(401, '登录已失效')
    }
    let payload: ApiEnvelope<T> & { error?: unknown; message?: unknown }
    try {
      const raw = await response.text()
      payload = (raw ? JSON.parse(raw) : {}) as ApiEnvelope<T> & { error?: unknown; message?: unknown }
    } catch {
      throw new ApiClientError(response.status, nonJsonResponseMessage(response.status))
    }
    if (!response.ok) throw new ApiClientError(response.status, errorMessage(payload))
    return payload
  }

  private redirectToLogin(): void {
    window.location.assign(LOGIN_PATH)
  }
}

function isAbortError(error: unknown): boolean {
  return error instanceof DOMException
    ? error.name === 'AbortError'
    : error instanceof Error && error.name === 'AbortError'
}

function formatTimeout(timeoutMs: number): string {
  if (timeoutMs % 1_000 === 0) return `${timeoutMs / 1_000} 秒`
  return `${timeoutMs} 毫秒`
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

function nonJsonResponseMessage(status: number): string {
  if ([502, 503, 504].includes(status)) {
    return `后端服务暂不可用（HTTP ${status}），可能正在部署或重启。请稍后点击刷新；持续出现请联系管理员检查 midscene-task 服务。`
  }
  if (status === 404) {
    return '接口地址不存在（HTTP 404），可能是前后端版本不一致。请联系管理员重新部署后端和页面。'
  }
  return `服务器返回格式异常（HTTP ${status || '未知'}），页面没有收到预期的 JSON 数据。请刷新重试；持续出现请联系管理员查看服务日志。`
}

export const apiClient = new ApiClient()
