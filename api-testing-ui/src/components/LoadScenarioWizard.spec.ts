// @vitest-environment jsdom

import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'
import LoadScenarioWizard from './LoadScenarioWizard.vue'

const endpoints = [
  { id: 'get-1', method: 'GET', path: '/models/search', summary: '搜索模型', tags: ['模型'] },
  { id: 'post-1', method: 'POST', path: '/orders', summary: '创建订单', tags: ['订单'] },
]

describe('LoadScenarioWizard', () => {
  it('keeps prior input across every step and emits a server-valid readonly definition', async () => {
    const wrapper = mount(LoadScenarioWizard, { props: { endpoints } })
    expect(wrapper.text()).toContain('单接口压测')
    expect(wrapper.text()).toContain('业务链路压测')
    await wrapper.get('[data-testid="load-scenario-name"]').setValue('模型搜索容量')
    await wrapper.get('[data-testid="scenario-endpoint-get-1"]').trigger('click')
    await wrapper.get('[data-testid="scenario-next"]').trigger('click')
    expect(wrapper.text()).toContain('循环共享')
    expect(wrapper.text()).toContain('每个用户固定一行')
    expect(wrapper.text()).toContain('每次迭代独占一行')
    await wrapper.get('[data-testid="scenario-next"]').trigger('click')
    expect(wrapper.text()).toContain('GET /models/search')
    await wrapper.get('[data-testid="scenario-back"]').trigger('click')
    await wrapper.get('[data-testid="scenario-back"]').trigger('click')
    expect((wrapper.get('[data-testid="load-scenario-name"]').element as HTMLInputElement).value).toBe('模型搜索容量')
    await wrapper.get('[data-testid="scenario-next"]').trigger('click')
    await wrapper.get('[data-testid="scenario-next"]').trigger('click')
    await wrapper.get('[data-testid="scenario-save"]').trigger('click')
    const definition = wrapper.emitted('save')?.[0]?.[0] as Record<string, unknown>
    expect(definition).toMatchObject({ name: '模型搜索容量', mode: 'single_interface' })
    expect(definition.steps).toEqual([expect.objectContaining({ scope: 'iteration', side_effect: 'readonly' })])
  })

  it('warns that write endpoints need owned-resource cleanup and supports cancel', async () => {
    const wrapper = mount(LoadScenarioWizard, { props: { endpoints } })
    await wrapper.get('[data-testid="scenario-mode-workflow"]').trigger('click')
    await wrapper.get('[data-testid="scenario-endpoint-post-1"]').trigger('click')
    expect(wrapper.text()).toContain('写接口必须说明资源归属并配置清理步骤')
    await wrapper.get('[data-testid="scenario-cancel"]').trigger('click')
    expect(wrapper.emitted('cancel')).toHaveLength(1)
  })
})
