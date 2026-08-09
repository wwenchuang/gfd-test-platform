// @vitest-environment jsdom

import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'

import CaseEditor from './CaseEditor.vue'
import type { CaseDraft } from '../api/contracts'

const DRAFT: CaseDraft = {
  name: '查询我的收藏',
  purpose: '确认收藏列表可读取',
  priority: 'P0',
  request: {
    method: 'GET', path: '/favorite/list', service: 'default',
    path_params: {}, query: { page: 1 }, headers: { Authorization: '{{ZXBToken}}' }, cookies: {}, body: null,
  },
  data_rows: [{ name: '默认数据', values: { page: 1 }, enabled: true }],
  assertions: [{ type: 'status_code', operator: 'equals', expected: 200, timeout_ms: 0, enabled: true }],
  extractions: [], dependencies: [], processing: { pre: [], post: [] },
}

describe('CaseEditor', () => {
  it('preserves unsaved structured edits while switching to raw JSON and back', async () => {
    const wrapper = mount(CaseEditor, { props: { modelValue: DRAFT } })

    await wrapper.find('[data-testid="case-name"]').setValue('收藏列表正向用例')
    await wrapper.find('[data-testid="raw-tab"]').trigger('click')
    await wrapper.find('[data-testid="structured-tab"]').trigger('click')

    expect((wrapper.find('[data-testid="case-name"]').element as HTMLInputElement).value).toBe('收藏列表正向用例')
    expect(wrapper.emitted('update:modelValue')?.at(-1)?.[0]).toMatchObject({ name: '收藏列表正向用例' })
  })

  it('shows validation feedback next to the affected field', () => {
    const wrapper = mount(CaseEditor, {
      props: { modelValue: DRAFT, validationErrors: { 'request.path': '请求路径不能为空' } },
    })

    expect(wrapper.find('[data-error-for="request.path"]').text()).toBe('请求路径不能为空')
  })

  it('keeps numeric assertion expectations numeric after editing', async () => {
    const wrapper = mount(CaseEditor, { props: { modelValue: DRAFT } })

    await wrapper.find('[data-testid="assertion-expected-0"]').setValue('201')

    const emitted = wrapper.emitted('update:modelValue')?.at(-1)?.[0] as CaseDraft
    expect(emitted.assertions[0].expected).toBe(201)
  })
})
