// @vitest-environment jsdom

import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'

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

    await wrapper.get('[data-testid="group-toggle-我的收藏"]').trigger('click')
    await wrapper.get('[data-testid="endpoint-endpoint-1"]').setValue(true)
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

  it('keeps complete names and paths available when compact rows truncate them', async () => {
    const wrapper = mount(EndpointTree, { props: { endpoints: FAVORITES } })

    await wrapper.get('[data-testid="group-toggle-我的收藏"]').trigger('click')
    expect(wrapper.get('.endpoint-copy strong').attributes('title')).toBe('收藏列表')
    expect(wrapper.get('.endpoint-copy small').attributes('title')).toBe('/favorite/list')
  })

  it('keeps the endpoint search controls sticky above the scrollable grouped list', () => {
    const wrapper = mount(EndpointTree, { props: { endpoints: FAVORITES } })

    expect(wrapper.get('.endpoint-search-bar').find('[data-testid="endpoint-search"]').exists()).toBe(true)
  })

  it('groups endpoints by Apifox folders and keeps ungrouped endpoints last', () => {
    const wrapper = mount(EndpointTree, {
      props: {
        endpoints: [
          {
            id: 'endpoint-ungrouped',
            method: 'GET',
            path: '/devices/list',
            summary: '设备列表',
            tags: [],
          },
          {
            id: 'endpoint-folder',
            method: 'POST',
            path: '/collection/add',
            summary: '添加收藏',
            tags: [],
            operation: {
              'x-apifox-folder': { path: ['家用业务', 'app接口', '我的收藏'], name: '我的收藏' },
            },
          },
          {
            id: 'endpoint-tags',
            method: 'GET',
            path: '/learning/report',
            summary: '成长报告',
            tags: ['家用业务', 'app接口', '成长报告'],
          },
        ],
      },
    })

    const groupTitles = wrapper.findAll('.endpoint-group h3').map(item => item.text())
    expect(groupTitles[0]).toContain('家用业务 / app接口 / 成长报告')
    expect(groupTitles[1]).toContain('家用业务 / app接口 / 我的收藏')
    expect(groupTitles.at(-1)).toContain('未分组接口')
  })

  it('starts synced groups collapsed, expands on demand, and toggles all endpoints in a group', async () => {
    const wrapper = mount(EndpointTree, {
      props: {
        endpoints: [
          { id: 'endpoint-1', method: 'GET', path: '/favorite/list', summary: '收藏列表', tags: ['我的收藏'] },
          { id: 'endpoint-2', method: 'POST', path: '/favorite/add', summary: '添加收藏', tags: ['我的收藏'] },
          { id: 'endpoint-3', method: 'GET', path: '/devices/list', summary: '设备列表', tags: ['设备'] },
        ],
      },
    })

    expect(wrapper.text()).toContain('我的收藏')
    expect(wrapper.text()).not.toContain('/favorite/list')

    await wrapper.get('[data-testid="group-toggle-我的收藏"]').trigger('click')
    expect(wrapper.text()).toContain('/favorite/list')

    await wrapper.get('[data-testid="group-select-我的收藏"]').setValue(true)

    expect(wrapper.emitted('selection-change')?.at(-1)?.[0]).toEqual(['endpoint-1', 'endpoint-2'])
    expect(wrapper.get('[data-testid="group-selected-count-我的收藏"]').text()).toContain('2 已选')
  })

  it('does not render a thousand endpoint rows until a group or search result is opened', async () => {
    const endpoints = Array.from({ length: 1000 }, (_, index) => ({
      id: `endpoint-${index}`,
      method: 'GET',
      path: `/large-list/${index}`,
      summary: `批量接口 ${index}`,
      tags: [`分组 ${index % 20}`],
    }))
    const wrapper = mount(EndpointTree, { props: { endpoints } })

    expect(wrapper.findAll('.endpoint-row')).toHaveLength(0)

    await wrapper.get('[data-testid="endpoint-search"]').setValue('/large-list/999')
    expect(wrapper.findAll('.endpoint-row')).toHaveLength(1)
    expect(wrapper.text()).toContain('批量接口 999')
  })

  it('auto-expands matching collapsed groups while searching', async () => {
    const wrapper = mount(EndpointTree, {
      props: {
        endpoints: [
          { id: 'endpoint-1', method: 'GET', path: '/favorite/list', summary: '收藏列表', tags: ['我的收藏'] },
          { id: 'endpoint-2', method: 'POST', path: '/favorite/add', summary: '添加收藏', tags: ['我的收藏'] },
        ],
      },
    })

    expect(wrapper.text()).not.toContain('/favorite/list')

    await wrapper.get('[data-testid="endpoint-search"]').setValue('收藏列表')

    expect(wrapper.text()).toContain('收藏列表')
    expect(wrapper.text()).toContain('/favorite/list')
  })

  it('highlights search matches in endpoint names, paths, and groups', async () => {
    const wrapper = mount(EndpointTree, {
      props: {
        endpoints: [
          { id: 'endpoint-1', method: 'POST', path: '/pmc/api/v1/iot/qidiAuth', summary: '获取设备密钥', tags: ['本地测试', '启迪设备'] },
        ],
      },
    })

    await wrapper.get('[data-testid="endpoint-search"]').setValue('qidi')

    const highlights = wrapper.findAll('mark.search-highlight').map(item => item.text())
    expect(highlights).toContain('qidi')
    expect(wrapper.text()).toContain('/pmc/api/v1/iot/qidiAuth')
  })

  it('shows selected endpoints separately and removes them without losing the full tree', async () => {
    const wrapper = mount(EndpointTree, {
      props: {
        endpoints: [
          { id: 'endpoint-1', method: 'GET', path: '/favorite/list', summary: '收藏列表', tags: ['我的收藏'] },
          { id: 'endpoint-2', method: 'POST', path: '/favorite/add', summary: '添加收藏', tags: ['我的收藏'] },
          { id: 'endpoint-3', method: 'GET', path: '/devices/list', summary: '设备列表', tags: ['设备'] },
        ],
        selectedIds: ['endpoint-1', 'endpoint-3'],
      },
    })

    await wrapper.get('[data-testid="selected-tab"]').trigger('click')

    expect(wrapper.text()).toContain('收藏列表')
    expect(wrapper.text()).toContain('设备列表')
    expect(wrapper.text()).not.toContain('添加收藏')

    await wrapper.get('[data-testid="remove-selected-endpoint-1"]').trigger('click')
    expect(wrapper.emitted('selection-change')?.at(-1)?.[0]).toEqual(['endpoint-3'])

    await wrapper.get('[data-testid="all-tab"]').trigger('click')
    await wrapper.get('[data-testid="group-toggle-我的收藏"]').trigger('click')
    expect(wrapper.text()).toContain('添加收藏')
  })

  it('opens the selected view by default when restoring a saved selection', () => {
    const wrapper = mount(EndpointTree, {
      props: {
        endpoints: FAVORITES,
        selectedIds: ['endpoint-1'],
        initialTab: 'selected',
      },
    })

    expect(wrapper.get('[data-testid="selected-tab"]').classes()).toContain('active')
    expect(wrapper.text()).toContain('收藏列表')
  })

  it('stays on all endpoints while the user builds a new selection', async () => {
    const wrapper = mount(EndpointTree, { props: { endpoints: FAVORITES } })

    await wrapper.get('[data-testid="group-toggle-我的收藏"]').trigger('click')
    await wrapper.get('[data-testid="endpoint-endpoint-1"]').setValue(true)
    await wrapper.setProps({ selectedIds: ['endpoint-1'] })

    expect(wrapper.get('[data-testid="all-tab"]').classes()).toContain('active')
    expect(wrapper.find('[data-testid="endpoint-endpoint-2"]').exists()).toBe(true)
  })

  it('filters selected endpoints by path as well as summary', async () => {
    const wrapper = mount(EndpointTree, {
      props: {
        endpoints: [
          { id: 'endpoint-1', method: 'GET', path: '/pmc/api/v1/deviceCmd/deviceStatus', summary: '获取设备状态', tags: ['本地测试', '启迪设备'] },
          { id: 'endpoint-2', method: 'POST', path: '/print3d/api/v1/qidi/collection/add', summary: '添加收藏', tags: ['家用业务', '我的收藏'] },
        ],
        selectedIds: ['endpoint-1', 'endpoint-2'],
      },
    })

    await wrapper.get('[data-testid="selected-tab"]').trigger('click')
    await wrapper.get('[data-testid="endpoint-search"]').setValue('qidi')

    expect(wrapper.text()).toContain('添加收藏')
    expect(wrapper.text()).toContain('/print3d/api/v1/qidi/collection/add')
    expect(wrapper.text()).not.toContain('获取设备状态')
    expect(wrapper.text()).not.toContain('/pmc/api/v1/deviceCmd/deviceStatus')
  })

  it('renders selected endpoint summaries in a dedicated readable row layout', async () => {
    const wrapper = mount(EndpointTree, {
      props: {
        endpoints: [
          { id: 'endpoint-1', method: 'GET', path: '/pmc/api/v1/deviceCmd/deviceStatus', summary: '获取设备状态', tags: ['本地测试', '启迪设备'] },
        ],
        selectedIds: ['endpoint-1'],
      },
    })

    await wrapper.get('[data-testid="selected-tab"]').trigger('click')

    const row = wrapper.get('[data-testid="selected-endpoint-row-endpoint-1"]')
    const summary = row.get('[data-testid="selected-endpoint-summary-endpoint-1"]')

    expect(row.classes()).toContain('selected-endpoint-row')
    expect(summary.text()).toContain('获取设备状态')
    expect(summary.text()).toContain('/pmc/api/v1/deviceCmd/deviceStatus')
    expect(row.get('[data-testid="remove-selected-endpoint-1"]').attributes('title')).toContain('移除 获取设备状态')
  })

  it('keeps selected endpoint rows wider than the remove button column in CSS', () => {
    const css = readFileSync(resolve(process.cwd(), 'src/styles/app.css'), 'utf8')

    expect(css).toContain('.endpoint-row.selected-endpoint-row { grid-template-columns: minmax(0, 1fr) 34px;')
  })
})
