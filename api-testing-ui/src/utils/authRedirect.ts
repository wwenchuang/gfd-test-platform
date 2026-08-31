const LOGIN_PAGE = '/task-manager.html'
const API_TEST_ROOT = '/api-test/'

type BrowserLocation = Pick<Location, 'pathname' | 'hash'> & {
  assign: (url: string) => void
}

function currentApiTestingRoute(location: BrowserLocation = window.location): string {
  const hash = location.hash.startsWith('#/') ? location.hash : '#/'
  return `${API_TEST_ROOT}${hash}`
}

export function apiTestingLoginPath(location: BrowserLocation = window.location): string {
  return `${LOGIN_PAGE}?return_to=${encodeURIComponent(currentApiTestingRoute(location))}`
}

export function redirectToApiTestingLogin(location: BrowserLocation = window.location): void {
  location.assign(apiTestingLoginPath(location))
}

export function requireApiTestingSession(location: BrowserLocation = window.location): boolean {
  if (sessionStorage.getItem('sessionToken')) return true
  redirectToApiTestingLogin(location)
  return false
}

export function requiresPasswordChange(payload: { code?: unknown; error?: unknown; profile?: { must_change_password?: boolean } }): boolean {
  const code = payload.error && typeof payload.error === 'object' && 'code' in payload.error
    ? payload.error.code : payload.code
  return payload.profile?.must_change_password === true || code === 'must_change_password' || code === 'password_change_required'
}

export async function verifyApiTestingSession(location: BrowserLocation = window.location): Promise<boolean> {
  if (!requireApiTestingSession(location)) return false
  const controller = new AbortController()
  const timeout = globalThis.setTimeout(() => controller.abort(), 15000)
  try {
    const response = await fetch('/api/auth/me', {
      headers: { Authorization: `Bearer ${sessionStorage.getItem('sessionToken')}` },
      credentials: 'same-origin', cache: 'no-store', signal: controller.signal,
    })
    if (response.status === 401) {
      sessionStorage.removeItem('sessionToken')
      sessionStorage.removeItem('user')
      redirectToApiTestingLogin(location)
      return false
    }
    const payload = await response.json()
    if (requiresPasswordChange(payload)) {
      redirectToApiTestingLogin(location)
      return false
    }
    if (response.status === 403) throw new Error('无权访问当前资源，请联系管理员确认角色和数据授权。')
    if (!response.ok || payload.ok === false) throw new Error('无法验证当前会话，请重试。')
    return true
  } finally { globalThis.clearTimeout(timeout) }
}
