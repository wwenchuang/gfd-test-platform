// @vitest-environment jsdom

import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'

import CaseEditor from './CaseEditor.vue'
import type { ApiEndpoint, CaseDraft } from '../api/contracts'

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

const WORKFLOW_ENDPOINTS: ApiEndpoint[] = [
  {
    id: 'resource-page',
    method: 'GET',
    path: '/resource/page',
    summary: '查询资源列表',
    tags: ['家用业务', '模型', '查询'],
  },
  {
    id: 'print-cancel',
    method: 'POST',
    path: '/printJob/cancel',
    summary: '取消打印',
    tags: ['打印任务'],
  },
]

describe('CaseEditor', () => {
  it('adds a setup step by selecting an endpoint from the current source revision', async () => {
    const wrapper = mount(CaseEditor, {
      props: { modelValue: DRAFT, endpointOptions: WORKFLOW_ENDPOINTS },
    })

    await wrapper.get('[data-testid="add-setup-step"]').trigger('click')
    await wrapper.get('[data-testid="setup-endpoint-0"]').setValue('resource-page')

    const emitted = wrapper.emitted('update:modelValue')?.at(-1)?.[0] as CaseDraft
    expect(emitted.processing.setup_steps![0]).toMatchObject({
      name: '查询资源列表',
      request: { method: 'GET', path: '/resource/page' },
    })
    expect(wrapper.text()).toContain('前置步骤')
    expect(wrapper.text()).toContain('主体请求')
    expect(wrapper.text()).toContain('清理步骤')
    expect(wrapper.findAll('optgroup').map(item => item.attributes('label'))).toContain('家用业务 / 模型 / 查询')
  })

  it('moves workflow steps without losing their request definitions', async () => {
    const draft = JSON.parse(JSON.stringify(DRAFT)) as CaseDraft
    draft.processing.setup_steps = [
      {
        name: '第一步', enabled: true,
        request: { method: 'GET', path: '/first', service: 'default', path_params: {}, query: {}, headers: {}, cookies: {}, body: null },
        assertions: [], extractions: [], required_variables: [],
      },
      {
        name: '第二步', enabled: true,
        request: { method: 'GET', path: '/second', service: 'default', path_params: {}, query: {}, headers: {}, cookies: {}, body: null },
        assertions: [], extractions: [], required_variables: [],
      },
    ]
    const wrapper = mount(CaseEditor, { props: { modelValue: draft } })

    await wrapper.get('[data-testid="setup-step-up-1"]').trigger('click')

    const emitted = wrapper.emitted('update:modelValue')?.at(-1)?.[0] as CaseDraft
    expect(emitted.processing.setup_steps!.map(item => item.name)).toEqual(['第二步', '第一步'])
    expect(emitted.processing.setup_steps![0].request.path).toBe('/second')
  })

  it('configures cleanup variables used to prevent incomplete cleanup requests', async () => {
    const wrapper = mount(CaseEditor, {
      props: { modelValue: DRAFT, endpointOptions: WORKFLOW_ENDPOINTS },
    })

    await wrapper.get('[data-testid="add-cleanup-step"]').trigger('click')
    await wrapper.get('[data-testid="cleanup-endpoint-0"]').setValue('print-cancel')
    await wrapper.get('[data-testid="cleanup-required-0"]').setValue('printTaskSn, deviceSn')

    const emitted = wrapper.emitted('update:modelValue')?.at(-1)?.[0] as CaseDraft
    expect(emitted.processing.cleanup_steps![0].required_variables).toEqual([
      'printTaskSn',
      'deviceSn',
    ])
  })

  it('configures bounded polling for an asynchronous query step', async () => {
    const wrapper = mount(CaseEditor, {
      props: { modelValue: DRAFT, endpointOptions: WORKFLOW_ENDPOINTS },
    })

    await wrapper.get('[data-testid="add-setup-step"]').trigger('click')
    await wrapper.get('[data-testid="setup-polling-0"]').setValue(true)
    await wrapper.get('[data-testid="setup-poll-attempts-0"]').setValue(12)
    await wrapper.get('[data-testid="setup-poll-interval-0"]').setValue(1500)

    const emitted = wrapper.emitted('update:modelValue')?.at(-1)?.[0] as CaseDraft
    expect(emitted.processing.setup_steps![0].polling).toEqual({
      max_attempts: 12,
      interval_ms: 1500,
    })
  })

  it('selects a dependency from grouped case options instead of requiring a version id', async () => {
    const wrapper = mount(CaseEditor, {
      props: {
        modelValue: DRAFT,
        dependencyOptions: [{
          id: 'setup-version-1',
          name: '添加收藏',
          group: '我的收藏',
          method: 'POST',
          path: '/collection/add',
          version: 1,
          exports: ['favoriteSn', 'modelSn'],
        }],
      },
    })

    await wrapper.get('[data-testid="add-dependency"]').trigger('click')
    await wrapper.get('[data-testid="dependency-case-0"]').setValue('setup-version-1')

    const emitted = wrapper.emitted('update:modelValue')?.at(-1)?.[0] as CaseDraft
    expect(emitted.dependencies).toEqual([{
      case_version_id: 'setup-version-1',
      required: true,
      exports: ['favoriteSn', 'modelSn'],
    }])
    expect(wrapper.text()).toContain('我的收藏')
    expect(wrapper.text()).toContain('添加收藏')
  })

  it('allows dependency exports to be selected explicitly', async () => {
    const draft = {
      ...DRAFT,
      dependencies: [{
        case_version_id: 'setup-version-1',
        required: true,
        exports: ['favoriteSn', 'modelSn'],
      }],
    }
    const wrapper = mount(CaseEditor, {
      props: {
        modelValue: draft,
        dependencyOptions: [{
          id: 'setup-version-1',
          name: '添加收藏',
          group: '我的收藏',
          method: 'POST',
          path: '/collection/add',
          version: 1,
          exports: ['favoriteSn', 'modelSn'],
        }],
      },
    })

    const modelExport = wrapper.get('[data-testid="dependency-export-0-modelSn"]')
    await modelExport.setValue(false)

    const emitted = wrapper.emitted('update:modelValue')?.at(-1)?.[0] as CaseDraft
    expect(emitted.dependencies[0].exports).toEqual(['favoriteSn'])
  })

  it('does not publish an empty placeholder when only adding a request header row', async () => {
    const wrapper = mount(CaseEditor, { props: { modelValue: DRAFT } })

    await wrapper.get('[data-testid="headers-add"]').trigger('click')

    expect(wrapper.find('[data-testid="headers-name"]').exists()).toBe(true)
    expect(wrapper.emitted('update:modelValue')).toBeUndefined()
  })

  it('does not include an unfinished request parameter when another field is edited', async () => {
    const wrapper = mount(CaseEditor, { props: { modelValue: DRAFT } })

    await wrapper.get('[data-testid="headers-add"]').trigger('click')
    await wrapper.get('[data-testid="case-name"]').setValue('收藏列表调整')

    const emitted = wrapper.emitted('update:modelValue')?.at(-1)?.[0] as CaseDraft
    expect(emitted.request.headers).toEqual({ Authorization: '{{ZXBToken}}' })
  })

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

  it('blocks business response codes entered as HTTP status assertions', async () => {
    const wrapper = mount(CaseEditor, { props: { modelValue: DRAFT } })

    await wrapper.find('[data-testid="assertion-expected-0"]').setValue('60101004')

    expect(wrapper.get('[data-error-for="assertions[0].expected"]').text()).toContain('响应 JSON 字段')
    expect(wrapper.get('[data-testid="save-case-draft"]').attributes('disabled')).toBeDefined()
  })

  it('writes request body edits back into request.body', async () => {
    const wrapper = mount(CaseEditor, { props: { modelValue: DRAFT } })

    await wrapper.find('[data-testid="request-body"]').setValue('{"favoriteId": 42}')

    const emitted = wrapper.emitted('update:modelValue')?.at(-1)?.[0] as CaseDraft
    expect(emitted.request.body).toEqual({ favoriteId: 42 })
    expect((emitted as unknown as Record<string, unknown>).body).toBeUndefined()
  })

  it('edits headers as rows instead of requiring a JSON object', async () => {
    const wrapper = mount(CaseEditor, { props: { modelValue: DRAFT } })

    await wrapper.find('[data-testid="headers-add"]').trigger('click')
    const names = wrapper.findAll('[data-testid="headers-name"]')
    const values = wrapper.findAll('[data-testid="headers-value"]')
    await names.at(-1)!.setValue('X-Biz')
    await values.at(-1)!.setValue('ZXB')

    const emitted = wrapper.emitted('update:modelValue')?.at(-1)?.[0] as CaseDraft
    expect(emitted.request.headers['X-Biz']).toBe('ZXB')
  })

  it('keeps request parameter values as strings even when they look numeric', async () => {
    const draft: CaseDraft = {
      ...DRAFT,
      request: {
        ...DRAFT.request,
        query: { deviceSn: '1234567890123456789' },
      },
    }
    const wrapper = mount(CaseEditor, { props: { modelValue: draft } })

    await wrapper.find('[data-testid="query-value"]').setValue('1234567890123456800')

    const emitted = wrapper.emitted('update:modelValue')?.at(-1)?.[0] as CaseDraft
    expect(emitted.request.query.deviceSn).toBe('1234567890123456800')
  })

  it('shows invalid JSON feedback without discarding the typed request body', async () => {
    const wrapper = mount(CaseEditor, { props: { modelValue: DRAFT } })
    const body = wrapper.find('[data-testid="request-body"]')

    await body.setValue('{broken')

    expect(wrapper.get('[data-error-for="request.body"]').text()).toContain('JSON')
    expect((body.element as HTMLTextAreaElement).value).toBe('{broken')
  })

  it('renders backend errors and warnings next to affected structured sections', () => {
    const wrapper = mount(CaseEditor, {
      props: {
        modelValue: DRAFT,
        validationErrors: {
          'request.headers.Authorization': 'Authorization 变量未配置',
          'assertions[0].expected': '状态码必须是整数',
        },
        validationWarnings: { 'data_rows[0]': '数据行没有覆盖边界值' },
      },
    })

    expect(wrapper.get('[data-error-for="request.headers.Authorization"]').text()).toContain('Authorization')
    expect(wrapper.get('[data-error-for="assertions[0].expected"]').text()).toContain('整数')
    expect(wrapper.get('[data-warning-for="data_rows[0]"]').text()).toContain('边界值')
  })

  it('shows missing structured request parameters in their owning sections', () => {
    const wrapper = mount(CaseEditor, {
      props: {
        modelValue: DRAFT,
        validationErrors: {
          'request.query.pageSize': '缺少查询参数 pageSize',
          'request.headers.X-Trace-Id': '缺少请求头 X-Trace-Id',
          'request.path_params.favoriteId': '缺少路径参数 favoriteId',
        },
      },
    })

    for (const [field, legend] of [
      ['request.query.pageSize', '查询参数'],
      ['request.headers.X-Trace-Id', '请求头'],
      ['request.path_params.favoriteId', '路径参数'],
    ]) {
      const feedback = wrapper.get(`[data-error-for="${field}"]`)
      expect(feedback.element.closest('fieldset')?.querySelector('legend')?.textContent).toBe(legend)
    }
  })

  it('shows nested request body validation errors in the body editor', () => {
    const wrapper = mount(CaseEditor, {
      props: {
        modelValue: DRAFT,
        validationErrors: { 'request.body.favorite.ownerId': '缺少收藏用户标识' },
      },
    })

    const feedback = wrapper.get('[data-error-for="request.body.favorite.ownerId"]')
    expect(feedback.element.closest('label')?.textContent).toContain('请求体（JSON）')
  })
})
