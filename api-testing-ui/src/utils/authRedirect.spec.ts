import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { apiTestingLoginPath, requireApiTestingSession } from './authRedirect'

describe('API testing login redirect', () => {
  const assign = vi.fn()
  const values = new Map<string, string>()

  beforeEach(() => {
    assign.mockReset()
    values.clear()
    vi.stubGlobal('window', {
      location: {
        pathname: '/api-test/',
        hash: '#/baselines?project_id=project-1',
        assign,
      },
    })
    vi.stubGlobal('sessionStorage', {
      getItem: (key: string) => values.get(key) ?? null,
    })
  })

  afterEach(() => vi.unstubAllGlobals())

  it('preserves the full hash route when building the login return path', () => {
    expect(apiTestingLoginPath()).toBe(
      '/task-manager.html?return_to=%2Fapi-test%2F%23%2Fbaselines%3Fproject_id%3Dproject-1',
    )
  })

  it('redirects before mounting when there is no session', () => {
    expect(requireApiTestingSession()).toBe(false)
    expect(assign).toHaveBeenCalledWith(apiTestingLoginPath())
  })

  it('allows bootstrap to continue when a session token exists', () => {
    values.set('sessionToken', 'session-value')

    expect(requireApiTestingSession()).toBe(true)
    expect(assign).not.toHaveBeenCalled()
  })
})
