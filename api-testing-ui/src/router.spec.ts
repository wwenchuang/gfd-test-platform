// @vitest-environment jsdom

import { describe, expect, it } from 'vitest'

import { router } from './router'

describe('application router loading', () => {
  it('loads every workspace page as a separate route chunk', () => {
    const workspaceRoutes = router.getRoutes()

    expect(workspaceRoutes.length).toBeGreaterThan(0)
    expect(workspaceRoutes.every(route => typeof route.components?.default === 'function')).toBe(true)
  })
})
