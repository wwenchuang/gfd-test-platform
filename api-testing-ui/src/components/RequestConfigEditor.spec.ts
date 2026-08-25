// @vitest-environment jsdom

import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'

import RequestConfigEditor from './RequestConfigEditor.vue'
import type { CaseRequest } from '../api/contracts'

const REQUEST: CaseRequest = {
  method: 'GET', path: '/resource/page', service: 'default',
  path_params: {}, query: {}, headers: {}, cookies: {}, body: null,
}

describe('RequestConfigEditor', () => {
  it('does not publish an unfinished placeholder row', async () => {
    const wrapper = mount(RequestConfigEditor, { props: { modelValue: REQUEST } })
    await wrapper.get('[data-testid="query-add"]').trigger('click')
    expect(wrapper.emitted('update:modelValue')).toBeUndefined()
  })

  it('edits request map values as strings', async () => {
    const wrapper = mount(RequestConfigEditor, { props: { modelValue: REQUEST } })
    await wrapper.get('[data-testid="query-add"]').trigger('click')
    await wrapper.get('[data-testid="query-name"]').setValue('pageSize')
    await wrapper.get('[data-testid="query-value"]').setValue('20')
    expect(wrapper.emitted('update:modelValue')?.at(-1)?.[0]).toMatchObject({ query: { pageSize: '20' } })
  })

  it('preserves OpenAPI scalar types while editing existing request values', async () => {
    const wrapper = mount(RequestConfigEditor, {
      props: {
        modelValue: { ...REQUEST, query: { pageNum: 1, includeDeleted: false } },
      },
    })

    const values = wrapper.findAll('[data-testid="query-value"]')
    await values[0].setValue('')
    await values[0].setValue('2')
    await values[1].setValue('')
    await values[1].setValue('true')

    const request = wrapper.emitted('update:modelValue')?.at(-1)?.[0] as CaseRequest
    expect(request.query).toEqual({ pageNum: 2, includeDeleted: true })
  })

  it('keeps invalid request body text visible and reports invalidity', async () => {
    const wrapper = mount(RequestConfigEditor, { props: { modelValue: REQUEST } })
    await wrapper.get('[data-testid="request-body"]').setValue('{broken')
    expect((wrapper.get('[data-testid="request-body"]').element as HTMLTextAreaElement).value).toBe('{broken')
    expect(wrapper.text()).toContain('JSON 格式不正确')
    expect(wrapper.emitted('validity')?.at(-1)).toEqual([false])
  })

  it('inserts a selected workflow variable into a request value', async () => {
    const wrapper = mount(RequestConfigEditor, {
      props: {
        modelValue: { ...REQUEST, query: { modelSn: '' } },
        variableOptions: [{ name: 'modelSn', source: '前置步骤 1 · 查询模型', sourceKind: 'setup', available: true }],
      },
    })
    await wrapper.get('[data-testid="query-variable-0"]').trigger('click')
    await wrapper.get('[data-testid="variable-insert-modelSn"]').trigger('click')
    expect(wrapper.emitted('update:modelValue')?.at(-1)?.[0]).toMatchObject({ query: { modelSn: '{{modelSn}}' } })
  })
})
