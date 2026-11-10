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
  it('opts a main GET into persisted IDs only when inserted and removes the flag with the last token', async () => {
    const wrapper = mount(RequestConfigEditor, { props: { modelValue: { ...REQUEST, query: { label: 'run-' } } } })
    await wrapper.get('[data-testid="query-variable-0"]').trigger('click')
    expect(wrapper.emitted('update:modelValue')).toBeUndefined()
    await wrapper.get('[data-testid="insert-execution-case-id"]').trigger('click')
    expect(wrapper.emitted('update:modelValue')?.at(-1)?.[0]).toMatchObject({
      run_id_binding: 1, query: { label: 'run-{{@execution_case_id}}' },
    })
    await wrapper.get('[data-testid="query-value"]').setValue('plain')
    expect(wrapper.emitted('update:modelValue')?.at(-1)?.[0]).not.toHaveProperty('run_id_binding')
  })

  it('restricts ID insertion to trace headers while leaving ordinary headers editable', () => {
    const wrapper = mount(RequestConfigEditor, { props: { modelValue: {
      ...REQUEST, headers: { Cookie:'trace=other', 'X-HTTP-Method-Override':'', 'x-correlation-id':'' },
    } } })
    expect(wrapper.find('[data-testid="headers-variable-0"]').exists()).toBe(false)
    expect(wrapper.find('[data-testid="headers-variable-1"]').exists()).toBe(false)
    expect(wrapper.find('[data-testid="headers-variable-2"]').exists()).toBe(true)
  })

  it('only offers persisted ID choices for the main read request', () => {
    for (const props of [
      { modelValue: { ...REQUEST, method: 'POST', query: { id: '' } }, prefix: 'request', allowRunIds: true },
      { modelValue: { ...REQUEST, query: { id: '' } }, prefix: 'request', allowRunIds: false },
      { modelValue: { ...REQUEST, query: { id: '' } }, prefix: 'processing.setup_steps.0.request', allowRunIds: true },
    ]) {
      expect(mount(RequestConfigEditor, { props }).find('[data-testid="query-variable-0"]').exists()).toBe(false)
    }
  })

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
