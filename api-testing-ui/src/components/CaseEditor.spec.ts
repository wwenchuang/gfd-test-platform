// @vitest-environment jsdom

import { mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import CaseEditor from './CaseEditor.vue'
import type { ApiEndpoint, CaseDraft } from '../api/contracts'
import { replaceBusinessLines } from '../utils/businessLines'

const DRAFT: CaseDraft = {
  name: '查询我的收藏',
  purpose: '确认收藏列表可读取',
  business: 'home',
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

function workflowDraft(): CaseDraft {
  const draft = JSON.parse(JSON.stringify(DRAFT)) as CaseDraft
  draft.processing.setup_steps = [
    {
      name: '查询模型', enabled: true,
      request: { method: 'GET', path: '/resource/page', service: 'default', path_params: {}, query: { page: 1 }, headers: {}, cookies: {}, body: null },
      assertions: [{ type: 'status_code', operator: 'equals', expected: 200, timeout_ms: 0, enabled: true }],
      extractions: [{ target: 'modelSn', type: 'json_path', path: '$.data.list[0].modelSn', required: true }],
      required_variables: [],
    },
    {
      name: '查询切片', enabled: true,
      request: { method: 'GET', path: '/slice/detail', service: 'default', path_params: {}, query: {}, headers: {}, cookies: {}, body: null },
      assertions: [], extractions: [], required_variables: ['modelSn'],
    },
  ]
  return draft
}

describe('CaseEditor', () => {
  beforeEach(() => replaceBusinessLines([
    { id: 'home', name: '家用', enabled: true },
    { id: 'shared', name: '共享', enabled: true },
  ]))

  it('shows and publishes the case business independently from the application', async () => {
    const wrapper = mount(CaseEditor, { props: { modelValue: DRAFT } })

    expect(wrapper.get('[data-testid="case-business-home"]').classes()).toContain('active')
    await wrapper.get('[data-testid="case-business-shared"]').trigger('click')

    const emitted = wrapper.emitted('update:modelValue')?.at(-1)?.[0] as CaseDraft
    expect(emitted.business).toBe('shared')
    expect(wrapper.text()).toContain('所属业务')
  })

  it('renders newly configured business lines by their Chinese names', async () => {
    replaceBusinessLines([{ id: 'biz_school', name: '校园版', enabled: true }])
    const draft = { ...DRAFT, business: 'biz_school' }
    const wrapper = mount(CaseEditor, { props: { modelValue: draft } })

    expect(wrapper.get('[data-testid="case-business-biz_school"]').text()).toBe('校园版')
    expect(wrapper.get('[data-testid="case-business-biz_school"]').classes()).toContain('active')
  })

  it('updates the selector when application configuration loads after the editor', async () => {
    const wrapper = mount(CaseEditor, { props: { modelValue: DRAFT } })

    replaceBusinessLines([{ id: 'biz_school', name: '校园版', enabled: true }])
    await wrapper.vm.$nextTick()

    expect(wrapper.get('[data-testid="case-business-biz_school"]').text()).toBe('校园版')
    expect(wrapper.text()).toContain('已停用或未配置')
  })

  it('opens endpoint selection without publishing a blank workflow step', async () => {
    const wrapper = mount(CaseEditor, {
      props: { modelValue: DRAFT, endpointOptions: WORKFLOW_ENDPOINTS },
    })

    await wrapper.get('[data-testid="add-setup-step"]').trigger('click')

    expect(wrapper.emitted('update:modelValue')).toBeUndefined()
    expect(wrapper.find('[data-testid="endpoint-picker-search"]').exists()).toBe(true)
  })

  it('offers save and save-and-debug in one sticky action bar', async () => {
    const wrapper = mount(CaseEditor, { props: { modelValue: DRAFT } })
    await wrapper.get('[data-testid="save-case-draft"]').trigger('click')
    await wrapper.get('[data-testid="save-and-debug"]').trigger('click')
    expect(wrapper.emitted('save')).toHaveLength(1)
    expect(wrapper.emitted('debug')).toHaveLength(1)
  })

  it('keeps optional sections collapsed until populated or invalid', () => {
    const draft = JSON.parse(JSON.stringify(DRAFT)) as CaseDraft
    draft.data_rows = []
    draft.extractions = []
    const empty = mount(CaseEditor, { props: { modelValue: draft } })
    expect(empty.get('[data-testid="data-rows-section"]').attributes('open')).toBeUndefined()
    expect(empty.get('[data-testid="extractions-section"]').attributes('open')).toBeUndefined()

    const invalid = mount(CaseEditor, {
      props: { modelValue: draft, validationErrors: { 'extractions[0].path': 'JSONPath 格式不正确' } },
    })
    expect(invalid.get('[data-testid="extractions-section"]').attributes('open')).toBeDefined()
  })

  it('adds a setup step by selecting an endpoint from the current source revision', async () => {
    const wrapper = mount(CaseEditor, {
      props: { modelValue: DRAFT, endpointOptions: WORKFLOW_ENDPOINTS },
    })

    await wrapper.get('[data-testid="add-setup-step"]').trigger('click')
    expect(wrapper.text()).toContain('家用业务 / 模型 / 查询')
    await wrapper.get('[data-testid="endpoint-picker-search"]').setValue('查询资源')
    await wrapper.get('[data-testid="endpoint-picker-option-resource-page"]').trigger('click')

    const emitted = wrapper.emitted('update:modelValue')?.at(-1)?.[0] as CaseDraft
    expect(emitted.processing.setup_steps![0]).toMatchObject({
      name: '查询资源列表',
      request: { method: 'GET', path: '/resource/page' },
    })
    expect(wrapper.text()).toContain('前置步骤')
    expect(wrapper.text()).toContain('主体请求')
    expect(wrapper.text()).toContain('清理步骤')
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

  it('keeps only the active workflow step expanded', async () => {
    const wrapper = mount(CaseEditor, { props: { modelValue: workflowDraft() } })

    expect(wrapper.findAll('[data-testid^="setup-step-body-"]')).toHaveLength(1)
    await wrapper.get('[data-testid="setup-step-toggle-1"]').trigger('click')

    expect(wrapper.find('[data-testid="setup-step-body-0"]').exists()).toBe(false)
    expect(wrapper.find('[data-testid="setup-step-body-1"]').exists()).toBe(true)
  })

  it('shows configuration counts and errors while a workflow step is collapsed', () => {
    const wrapper = mount(CaseEditor, {
      props: {
        modelValue: workflowDraft(),
        validationErrors: { 'processing.setup_steps[1].request.path': '请求路径不能为空' },
      },
    })

    const summary = wrapper.get('[data-testid="setup-step-summary-1"]')
    expect(summary.text()).toContain('断言 0')
    expect(summary.text()).toContain('错误 1')
  })

  it('duplicates a workflow step and confirms before deleting it', async () => {
    const wrapper = mount(CaseEditor, { props: { modelValue: workflowDraft() } })

    await wrapper.get('[data-testid="setup-step-duplicate-0"]').trigger('click')
    let emitted = wrapper.emitted('update:modelValue')?.at(-1)?.[0] as CaseDraft
    expect(emitted.processing.setup_steps?.map(step => step.name)).toEqual(['查询模型', '查询模型 副本', '查询切片'])

    const confirm = vi.spyOn(globalThis, 'confirm').mockReturnValue(true)
    await wrapper.get('[data-testid="setup-step-remove-1"]').trigger('click')
    emitted = wrapper.emitted('update:modelValue')?.at(-1)?.[0] as CaseDraft
    expect(confirm).toHaveBeenCalledWith('确认删除步骤“查询模型 副本”？')
    expect(emitted.processing.setup_steps?.map(step => step.name)).toEqual(['查询模型', '查询切片'])
    confirm.mockRestore()
  })

  it('edits workflow request assertions and extractions without requiring raw JSON', async () => {
    const wrapper = mount(CaseEditor, { props: { modelValue: workflowDraft() } })

    expect(wrapper.find('[data-testid="setup-0-query-add"]').exists()).toBe(true)
    expect(wrapper.find('[data-testid="setup-0-add-assertion"]').exists()).toBe(true)
    expect(wrapper.find('[data-testid="setup-0-add-extraction"]').exists()).toBe(true)
    expect(wrapper.find('[data-testid="setup-0-raw-config"]').attributes('open')).toBeUndefined()
  })

  it('configures cleanup variables used to prevent incomplete cleanup requests', async () => {
    const draft = JSON.parse(JSON.stringify(DRAFT)) as CaseDraft
    draft.extractions = [{ target: 'printTaskSn', type: 'json_path', path: '$.data.taskSn', required: true }]
    const wrapper = mount(CaseEditor, {
      props: { modelValue: draft, endpointOptions: WORKFLOW_ENDPOINTS, environmentVariableNames: ['deviceSn'] },
    })

    await wrapper.get('[data-testid="add-cleanup-step"]').trigger('click')
    await wrapper.get('[data-testid="endpoint-picker-search"]').setValue('取消打印')
    await wrapper.get('[data-testid="endpoint-picker-option-print-cancel"]').trigger('click')
    await wrapper.get('[data-testid="cleanup-0-variable-option-printTaskSn"]').trigger('click')
    await wrapper.get('[data-testid="cleanup-0-variable-option-deviceSn"]').trigger('click')

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
    await wrapper.get('[data-testid="endpoint-picker-manual"]').trigger('click')
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
    await wrapper.get('[data-testid="dependency-search"]').setValue('添加收藏')
    await wrapper.get('[data-testid="dependency-option-setup-version-1"]').trigger('click')

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
