// @vitest-environment jsdom

import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'
import type { LoadScenarioDefinition } from '../api/contracts'
import LoadScenarioWizard from './LoadScenarioWizard.vue'

const endpoints = [
  { id: 'get-1', method: 'GET', path: '/models/search', summary: '搜索模型', tags: ['模型'] },
  { id: 'post-1', method: 'POST', path: '/orders', summary: '创建订单', tags: ['订单'] },
]

describe('LoadScenarioWizard', () => {
  it('preserves complete existing definitions even when endpoint source assets disappeared', async () => {
    const definition: LoadScenarioDefinition = {
      name: '保留复杂链路', description: '历史版本', mode: 'workflow',
      steps: [{ id: 'read', name: '业务查询', scope: 'vu_once', action: 'http_request',
        request: { method: 'GET', path: '/models', service: 'catalog', path_params: {}, query: { keyword: { $data: 'keyword' } }, headers: {}, cookies: {}, body: null },
        assertions: [{ type: 'json_path', path: '$.code', operator: 'equals', expected: 0 }],
        extractions: [{ source: 'json_body', expression: '$.data.id', target: 'model_id' }], sleep_ms: 250, side_effect: 'readonly' }],
      dataset_contract: { dataset_id: 'dataset-original', usage_mode: 'fixed_per_vu', variables: ['keyword'] },
      risk: { level: 'medium', ownership_variable: 'model_id', notes: '保留风险说明' },
      source_snapshot: { type: 'case', items: [{ id: 'old-unavailable-source' }] },
    }
    const original = JSON.parse(JSON.stringify(definition))
    const wrapper = mount(LoadScenarioWizard, { props: { endpoints: [], initialDefinition: definition } })
    expect(wrapper.get('[data-testid="scenario-next"]').attributes('disabled')).toBeUndefined()
    await wrapper.get('[data-testid="load-scenario-name"]').setValue('修改名称')
    await wrapper.get('[data-testid="scenario-next"]').trigger('click')
    await wrapper.get('[data-testid="scenario-next"]').trigger('click')
    await wrapper.get('[data-testid="scenario-save"]').trigger('click')
    expect(wrapper.emitted('save')?.[0]?.[0]).toEqual({ ...definition, name: '修改名称' })
    expect(definition).toEqual(original)
  })

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

  it('keeps invalid advanced input visible and blocks next until it is applied', async () => {
    const wrapper = mount(LoadScenarioWizard, { props: { endpoints } })
    await wrapper.get('[data-testid="load-scenario-name"]').setValue('断言配置')
    await wrapper.get('[data-testid="scenario-endpoint-get-1"]').trigger('click')
    await wrapper.get('[data-testid="scenario-next"]').trigger('click')
    expect(wrapper.find('[data-testid="scenario-definition-json"]').exists()).toBe(true)
    const editor = wrapper.get('[data-testid="scenario-definition-json"]')
    const definition = JSON.parse((editor.element as HTMLTextAreaElement).value)
    await editor.setValue('{ invalid')
    await wrapper.get('[data-testid="scenario-definition-apply"]').trigger('click')
    expect(wrapper.text()).toContain('JSON 格式无效')
    expect(wrapper.get('[data-testid="scenario-next"]').attributes('disabled')).toBeDefined()
    await wrapper.get('[data-testid="scenario-back"]').trigger('click')
    expect(wrapper.get('[data-testid="load-scenario-name"]').isVisible()).toBe(true)
    await wrapper.get('[data-testid="scenario-next"]').trigger('click')
    expect((wrapper.get('[data-testid="scenario-definition-json"]').element as HTMLTextAreaElement).value).toBe('{ invalid')
    definition.steps[0].assertions.push({ type: 'json_path', path: '$.code', operator: 'equals', expected: 0 })
    await editor.setValue(JSON.stringify(definition))
    await wrapper.get('[data-testid="scenario-definition-apply"]').trigger('click')
    await wrapper.get('[data-testid="scenario-next"]').trigger('click')
    await wrapper.get('[data-testid="scenario-save"]').trigger('click')
    expect(wrapper.emitted('save')?.[0]?.[0]).toEqual(definition)
  })
})
