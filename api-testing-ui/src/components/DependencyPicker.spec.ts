// @vitest-environment jsdom

import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'

import DependencyPicker from './DependencyPicker.vue'

const OPTIONS = [
  { id: 'login-version', name: '登录并获取令牌', group: '账号', method: 'POST', path: '/login', version: 2, exports: ['loginToken'] },
  { id: 'favorite-version', name: '添加收藏', group: '模型收藏', method: 'POST', path: '/collection/add', version: 1, exports: ['favoriteId'] },
]

describe('DependencyPicker', () => {
  it('searches shared dependencies without requiring a version id', async () => {
    const wrapper = mount(DependencyPicker, { props: { modelValue: '', options: OPTIONS, disabledIds: [] } })
    await wrapper.get('[data-testid="dependency-search"]').setValue('添加收藏')
    await wrapper.get('[data-testid="dependency-option-favorite-version"]').trigger('click')
    expect(wrapper.emitted('update:modelValue')?.[0]).toEqual(['favorite-version'])
  })

  it('finds a dependency by exported variable and disables duplicates', async () => {
    const wrapper = mount(DependencyPicker, { props: { modelValue: '', options: OPTIONS, disabledIds: ['favorite-version'] } })
    await wrapper.get('[data-testid="dependency-search"]').setValue('favoriteId')
    expect(wrapper.get('[data-testid="dependency-option-favorite-version"]').attributes('disabled')).toBeDefined()
  })
})
