import { afterEach, describe, expect, it, vi } from 'vitest'

import { createIdempotencyKey } from './idempotency'

describe('createIdempotencyKey', () => {
  afterEach(() => vi.unstubAllGlobals())

  it('uses crypto.randomUUID when the browser provides it', () => {
    vi.stubGlobal('crypto', { randomUUID: () => 'native-uuid' })

    expect(createIdempotencyKey()).toBe('native-uuid')
  })

  it('creates an RFC 4122 UUID when randomUUID is unavailable on HTTP pages', () => {
    vi.stubGlobal('crypto', {
      getRandomValues: (values: Uint8Array) => {
        values.set([0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15])
        return values
      },
    })

    expect(createIdempotencyKey()).toBe('00010203-0405-4607-8809-0a0b0c0d0e0f')
  })
})
