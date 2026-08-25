// @vitest-environment jsdom

import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'

import type { CaseGroupNode } from '../utils/caseListPresentation'
import CaseGroupBranch from './CaseGroupBranch.vue'

const TREE = {
  id: '家用业务',
  label: '家用业务',
  fullPath: '家用业务',
  count: 2,
  items: [],
  children: [{
    id: '家用业务 / 我的收藏',
    label: '我的收藏',
    fullPath: '家用业务 / 我的收藏',
    count: 2,
    items: [
      { id: 'case-1', kind: 'version', name: '添加收藏' },
      { id: 'case-2', kind: 'version', name: '取消收藏' },
    ],
    children: [],
  }],
} as unknown as CaseGroupNode

describe('CaseGroupBranch', () => {
  it('renders descendants only when each directory is expanded', async () => {
    const wrapper = mount(CaseGroupBranch, {
      props: { node: TREE, expandedIds: ['家用业务'], query: '' },
      slots: { item: '<template #item="{ item }"><div :data-testid="`item-${item.id}`">{{ item.name }}</div></template>' },
    })

    expect(wrapper.get('[data-testid="case-list-group-toggle-家用业务"]').attributes('aria-expanded')).toBe('true')
    expect(wrapper.get('[data-testid="case-list-group-toggle-家用业务 / 我的收藏"]').attributes('aria-expanded')).toBe('false')
    expect(wrapper.find('[data-testid="item-case-1"]').exists()).toBe(false)

    await wrapper.get('[data-testid="case-list-group-toggle-家用业务 / 我的收藏"]').trigger('click')
    expect(wrapper.emitted('toggle')?.[0]).toEqual(['家用业务 / 我的收藏'])
  })

  it('forces the complete matching branch open and highlights its label', () => {
    const wrapper = mount(CaseGroupBranch, {
      props: { node: TREE, expandedIds: [], query: '收藏', forceExpanded: true },
      slots: { item: '<template #item="{ item }"><div :data-testid="`item-${item.id}`">{{ item.name }}</div></template>' },
    })

    expect(wrapper.get('[data-testid="case-list-group-toggle-家用业务"]').attributes('aria-expanded')).toBe('true')
    expect(wrapper.get('[data-testid="case-list-group-toggle-家用业务 / 我的收藏"]').attributes('aria-expanded')).toBe('true')
    expect(wrapper.get('[data-testid="case-list-group-家用业务 / 我的收藏"] mark').text()).toBe('收藏')
    expect(wrapper.get('[data-testid="item-case-1"]').text()).toBe('添加收藏')
  })
})
