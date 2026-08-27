// @vitest-environment jsdom

import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'

import type { ApiEndpoint } from '../api/contracts'
import CaseEndpointPicker from './CaseEndpointPicker.vue'

const ENDPOINTS: ApiEndpoint[] = [
  { id: 'endpoint-favorite', method: 'GET', path: '/favorite/list', summary: '收藏列表', tags: ['家用业务'] },
  { id: 'endpoint-epone', method: 'POST', path: '/api/v1/devices/editFt', summary: '保存设备耗材（EPOne 复用）', tags: ['家用业务', 'EPOne设备接入'] },
]

describe('CaseEndpointPicker', () => {
  it('searches endpoints and launches manual, basic, and AI creation flows', async () => {
    const wrapper = mount(CaseEndpointPicker, {
      props: {
        endpoints: ENDPOINTS,
        caseCountByEndpoint: { 'endpoint-favorite': 1 },
      },
    })

    expect(wrapper.text()).toContain('选择接口创建用例')
    expect(wrapper.text()).toContain('输入接口名称、路径或分组进行搜索')

    await wrapper.get('[data-testid="case-endpoint-search"]').setValue('EPOne')
    await wrapper.get('[data-testid="case-endpoint-endpoint-epone"]').trigger('click')

    expect(wrapper.text()).toContain('保存设备耗材（EPOne 复用）')
    expect(wrapper.text()).toContain('该接口暂无用例')

    await wrapper.get('[data-testid="case-endpoint-create-manual"]').trigger('click')
    await wrapper.get('[data-testid="case-endpoint-generate-basic"]').trigger('click')
    await wrapper.get('[data-testid="case-endpoint-generate-ai"]').trigger('click')

    expect(wrapper.emitted('create-manual')?.[0]).toEqual([ENDPOINTS[1]])
    expect(wrapper.emitted('generate-basic')?.[0]).toEqual([ENDPOINTS[1]])
    expect(wrapper.emitted('generate-ai')?.[0]).toEqual([ENDPOINTS[1]])
  })

  it('limits the unfiltered list and explains how to find all interfaces', () => {
    const endpoints = Array.from({ length: 120 }, (_, index): ApiEndpoint => ({
      id: `endpoint-${index}`, method: 'GET', path: `/items/${index}`, summary: `接口 ${index}`, tags: ['批量接口'],
    }))
    const wrapper = mount(CaseEndpointPicker, { props: { endpoints, caseCountByEndpoint: {} } })

    expect(wrapper.findAll('[data-testid^="case-endpoint-endpoint-"]')).toHaveLength(50)
    expect(wrapper.text()).toContain('当前展示前 50 个接口')
  })
})
