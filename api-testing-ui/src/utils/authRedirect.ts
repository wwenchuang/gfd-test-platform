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
