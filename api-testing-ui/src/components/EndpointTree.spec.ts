// @vitest-environment jsdom

import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'

import EndpointTree from './EndpointTree.vue'

const FAVORITES = [
  { id: 'endpoint-1', method: 'GET', path: '/favorite/list', summary: '收藏列表', tags: ['我的收藏'] },
  { id: 'endpoint-2', method: 'POST', path: '/favorite/cancel', summary: '删除收藏', tags: ['我的收藏'] },
]

describe('EndpointTree', () => {
  it('keeps endpoint selection while filtering the tree', async () => {
    const wrapper = mount(EndpointTree, { props: { endpoints: FAVORITES } })

    await wrapper.find('[data-testid="endpoint-endpoint-1"]').setValue(true)
    await wrapper.find('[data-testid="endpoint-search"]').setValue('删除收藏')

    expect(wrapper.emitted('selection-change')?.at(-1)?.[0]).toEqual(['endpoint-1'])
    expect(wrapper.text()).toContain('删除收藏')
    expect(wrapper.text()).not.toContain('收藏列表')
  })

  it('exposes loading, failed, empty, and partial result states', () => {
    expect(mount(EndpointTree, { props: { endpoints: [], state: 'loading' } }).text()).toContain('正在读取接口')
    expect(mount(EndpointTree, { props: { endpoints: [], state: 'failed', error: '读取失败' } }).text()).toContain('读取失败')
    expect(mount(EndpointTree, { props: { endpoints: [], state: 'empty' } }).text()).toContain('尚无已保存接口')
    expect(mount(EndpointTree, { props: { endpoints: FAVORITES, state: 'partial' } }).text()).toContain('部分接口未能读取')
  })

  it('keeps complete names and paths available when compact rows truncate them', () => {
    const wrapper = mount(EndpointTree, { props: { endpoints: FAVORITES } })

    expect(wrapper.get('.endpoint-copy strong').attributes('title')).toBe('收藏列表')
    expect(wrapper.get('.endpoint-copy small').attributes('title')).toBe('/favorite/list')
  })
})
