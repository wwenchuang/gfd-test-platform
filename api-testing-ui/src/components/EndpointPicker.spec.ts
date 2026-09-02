// @vitest-environment jsdom

import { mount } from '@vue/test-utils'
import { nextTick } from 'vue'
import { describe, expect, it } from 'vitest'

import EndpointPicker from './EndpointPicker.vue'
import type { ApiEndpoint } from '../api/contracts'

const ENDPOINTS: ApiEndpoint[] = [
  { id: 'resource-page', method: 'GET', path: '/resource/page', summary: '查询资源列表', tags: ['家用业务', '模型'] },
  { id: 'print-cancel', method: 'POST', path: '/printJob/cancel', summary: '取消打印', tags: ['家用业务', '打印任务'] },
]

describe('EndpointPicker', () => {
  it('keeps groups collapsed until the user searches or expands one', async () => {
    const wrapper = mount(EndpointPicker, {
      props: { open: true, title: '添加前置步骤', endpoints: ENDPOINTS },
    })

    expect(wrapper.find('[data-testid="endpoint-picker-option-resource-page"]').exists()).toBe(false)
    await wrapper.get('[data-testid="endpoint-picker-group-家用业务 / 模型"]').trigger('click')
    expect(wrapper.find('[data-testid="endpoint-picker-option-resource-page"]').exists()).toBe(true)
  })

  it('consolidates a large step catalog by business before folder groups', async () => {
    const endpoints: ApiEndpoint[] = [
      ...Array.from({ length: 10 }, (_, index) => ({
        id: `home-${index}`,
        method: 'GET',
        path: `/home/${index}`,
        summary: `家用步骤 ${index}`,
        tags: ['家用业务', 'app接口', `分组 ${index}`],
      })),
      ...Array.from({ length: 3 }, (_, index) => ({
        id: `shared-${index}`,
        method: 'POST',
        path: `/shared/${index}`,
        summary: `共享步骤 ${index}`,
        tags: ['共享业务', `分组 ${index}`],
      })),
    ]
    const wrapper = mount(EndpointPicker, {
      props: { open: true, title: '添加前置步骤', endpoints },
    })

    expect(wrapper.get('[data-testid="endpoint-picker-domain-家用"]').text()).toContain('10 个分组 · 10 个接口')
    expect(wrapper.get('[data-testid="endpoint-picker-domain-共享"]').text()).toContain('3 个分组 · 3 个接口')
    expect(wrapper.find('[data-testid="endpoint-picker-group-家用业务 / app接口 / 分组 0"]').exists()).toBe(false)

    await wrapper.get('[data-testid="endpoint-picker-domain-家用"]').trigger('click')
    expect(wrapper.get('[data-testid="endpoint-picker-group-家用业务 / app接口 / 分组 0"]').text()).toContain('app接口 / 分组 0')

    await wrapper.get('[data-testid="endpoint-picker-search"]').setValue('共享步骤 2')
    expect(wrapper.text()).toContain('共享步骤 2')
    expect(wrapper.text()).toContain('/shared/2')
  })

  it('searches endpoint name path method and group with highlighted matches', async () => {
    const wrapper = mount(EndpointPicker, {
      props: { open: true, title: '添加前置步骤', endpoints: ENDPOINTS },
    })

    await wrapper.get('[data-testid="endpoint-picker-search"]').setValue('cancel')

    expect(wrapper.text()).toContain('/printJob/cancel')
    expect(wrapper.findAll('mark').map(node => node.text().toLowerCase())).toContain('cancel')
    expect(wrapper.text()).not.toContain('/resource/page')
  })

  it('emits the selected endpoint', async () => {
    const wrapper = mount(EndpointPicker, {
      props: { open: true, title: '添加清理步骤', endpoints: ENDPOINTS },
    })

    await wrapper.get('[data-testid="endpoint-picker-search"]').setValue('取消打印')
    await wrapper.get('[data-testid="endpoint-picker-option-print-cancel"]').trigger('click')

    expect(wrapper.emitted('select')?.[0]?.[0]).toMatchObject({ id: 'print-cancel' })
  })

  it('focuses search on open and closes on Escape', async () => {
    const trigger = document.createElement('button')
    document.body.appendChild(trigger)
    trigger.focus()
    const wrapper = mount(EndpointPicker, {
      attachTo: document.body,
      props: { open: true, title: '添加前置步骤', endpoints: ENDPOINTS },
    })
    await nextTick()

    expect(document.activeElement).toBe(wrapper.get('[data-testid="endpoint-picker-search"]').element)
    await wrapper.get('[role="dialog"]').trigger('keydown', { key: 'Escape' })
    expect(wrapper.emitted('close')).toHaveLength(1)

    wrapper.unmount()
    trigger.remove()
  })
})
