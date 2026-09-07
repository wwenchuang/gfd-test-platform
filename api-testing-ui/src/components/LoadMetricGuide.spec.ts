// @vitest-environment jsdom
import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'
import LoadMetricGuide from './LoadMetricGuide.vue'

describe('LoadMetricGuide', () => {
  it('opens and collapses the glossary without losing the query', async () => {
    const wrapper = mount(LoadMetricGuide)
    const toggle = wrapper.get('button')
    expect(toggle.attributes('aria-expanded')).toBe('false')
    expect(wrapper.find('input').exists()).toBe(false)
    await toggle.trigger('click')
    expect(toggle.attributes('aria-expanded')).toBe('true')
    await wrapper.get('input').setValue('p95')
    expect(wrapper.findAll('article')).toHaveLength(1)
    expect(wrapper.get('article').text()).toContain('不能平均各节点或各时段的 P95')
    await toggle.trigger('click')
    await toggle.trigger('click')
    expect((wrapper.get('input').element as HTMLInputElement).value).toBe('p95')
    expect(wrapper.findAll('article')).toHaveLength(1)
  })

  it('preserves Chinese composition and filters only after committed input', async () => {
    const wrapper = mount(LoadMetricGuide)
    await wrapper.get('button').trigger('click')
    const input = wrapper.get('input')
    const initialCount = wrapper.findAll('article').length
    await input.trigger('compositionstart')
    await input.setValue('ziyuan')
    expect(wrapper.findAll('article')).toHaveLength(initialCount)
    ;(input.element as HTMLInputElement).value = '资源'
    await input.trigger('compositionend')
    expect(wrapper.findAll('article')).toHaveLength(1)
    expect(wrapper.get('article').text()).toContain('压测 Agent')
    await input.setValue('不存在的指标')
    expect(wrapper.get('[role="status"]').text()).toContain('没有匹配')
    await input.setValue('')
    expect(wrapper.findAll('article')).toHaveLength(initialCount)
  })
})
