// @vitest-environment jsdom

import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'

import EndpointDetail from './EndpointDetail.vue'

const endpoint = {
  id: 'endpoint-1',
  stable_key: 'stable-favorites-list',
  operation_id: 'listFavorites',
  method: 'GET',
  path: '/collection/list',
  summary: '收藏列表',
  tags: ['家用', '收藏'],
  operation: {
    parameters: [
      { name: 'pageNum', in: 'query', required: true, description: '页码', schema: { type: 'integer' } },
    ],
    responses: {
      200: { description: '成功', content: { 'application/json': { schema: { type: 'object' } } } },
    },
  },
}

describe('EndpointDetail', () => {
  it('shows structured request and response summaries with optional raw JSON', async () => {
    const wrapper = mount(EndpointDetail, { props: { endpoint } })

    await wrapper.findAll('.detail-tabs button')[1].trigger('click')
    expect(wrapper.text()).toContain('pageNum')
    expect(wrapper.text()).toContain('Query 参数')
    expect(wrapper.text()).toContain('必填')
    expect(wrapper.text()).toContain('查看原始 JSON')

    await wrapper.findAll('.detail-tabs button')[2].trigger('click')
    expect(wrapper.text()).toContain('200')
    expect(wrapper.text()).toContain('application/json')
  })

  it('opens the execution page filtered to the selected endpoint', async () => {
    const wrapper = mount(EndpointDetail, { props: { endpoint } })

    await wrapper.findAll('.detail-tabs button')[4].trigger('click')
    await wrapper.get('[data-testid="endpoint-open-history"]').trigger('click')

    expect(wrapper.emitted('open-history')).toEqual([['endpoint-1', 'stable-favorites-list']])
  })
})
