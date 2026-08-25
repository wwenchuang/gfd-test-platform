import { describe, expect, it } from 'vitest'

import { environmentServicePresentation } from './environmentPresentation'

describe('environmentServicePresentation', () => {
  it('groups service keys that resolve to the same effective base URL', () => {
    const result = environmentServicePresentation({
      default: { name: 'default', module_name: '默认服务', base_url: 'https://api.example.com/', unresolved: false },
      image: { name: 'image', module_name: '图片建模', base_url: ' https://api.example.com ', unresolved: false },
      file: { name: 'file', module_name: '文件服务', base_url: 'https://file.example.com', unresolved: false },
    })

    expect(result.effectiveAddressCount).toBe(2)
    expect(result.serviceKeyCount).toBe(3)
    expect(result.unconfiguredKeyCount).toBe(0)
    expect(result.groups).toEqual([
      {
        id: 'https://api.example.com',
        baseUrl: 'https://api.example.com',
        configured: true,
        serviceKeys: ['default', 'image'],
        labels: ['默认服务', '图片建模'],
      },
      {
        id: 'https://file.example.com',
        baseUrl: 'https://file.example.com',
        configured: true,
        serviceKeys: ['file'],
        labels: ['文件服务'],
      },
    ])
  })

  it('groups empty service addresses without counting them as effective addresses', () => {
    const result = environmentServicePresentation({
      default: { name: 'default', module_name: '默认服务', base_url: null, unresolved: true },
      image: { name: 'image', module_name: '图片建模', base_url: '  ', unresolved: true },
    })

    expect(result.effectiveAddressCount).toBe(0)
    expect(result.serviceKeyCount).toBe(2)
    expect(result.unconfiguredKeyCount).toBe(2)
    expect(result.groups).toEqual([
      {
        id: '__unconfigured__',
        baseUrl: '',
        configured: false,
        serviceKeys: ['default', 'image'],
        labels: ['默认服务', '图片建模'],
      },
    ])
  })
})
