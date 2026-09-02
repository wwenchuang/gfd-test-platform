// @vitest-environment jsdom

import { describe, expect, it } from 'vitest'

import { installImeCompositionGuard } from './imeCompositionGuard'

describe('installImeCompositionGuard', () => {
  it('protects all text search surfaces from intermediate Chinese IME input events', () => {
    const cleanup = installImeCompositionGuard(document)
    const inputs = [
      Object.assign(document.createElement('input'), { type: 'search' }),
      Object.assign(document.createElement('input'), { type: 'text' }),
      document.createElement('textarea'),
    ]

    for (const input of inputs) {
      let inputCount = 0
      input.addEventListener('input', () => { inputCount += 1 })
      document.body.append(input)
      input.value = 'shoucang'
      input.dispatchEvent(new InputEvent('input', {
        bubbles: true,
        data: 'g',
        inputType: 'insertCompositionText',
        isComposing: true,
      }))
      expect(inputCount).toBe(0)

      input.value = '收藏'
      input.dispatchEvent(new InputEvent('input', { bubbles: true, data: '收藏' }))
      expect(inputCount).toBe(1)
      input.remove()
    }

    cleanup()
  })

  it('commits the final Chinese value when Safari emits no input after compositionend', async () => {
    const cleanup = installImeCompositionGuard(document)
    const input = document.createElement('input')
    input.type = 'text'
    document.body.append(input)
    const values: string[] = []
    input.addEventListener('input', () => { values.push(input.value) })

    input.dispatchEvent(new CompositionEvent('compositionstart', { bubbles: true, data: '' }))
    input.value = 'zhongwen'
    input.dispatchEvent(new InputEvent('input', {
      bubbles: true,
      data: 'n',
      inputType: 'insertCompositionText',
      isComposing: true,
    }))
    input.value = '中文'
    input.dispatchEvent(new CompositionEvent('compositionend', { bubbles: true, data: '中文' }))

    await new Promise(resolve => setTimeout(resolve, 0))
    expect(values).toEqual(['中文'])

    input.remove()
    cleanup()
  })
})
